const { Connection, Keypair, PublicKey, VersionedTransaction } = require('@solana/web3.js');
const bs58 = require('bs58');
const axios = require('axios');

// Rate limiting constants
const MAX_RETRIES = 5;
const INITIAL_RETRY_DELAY = 2000; // 2 seconds

// Print Node.js version for debugging
console.log(`Node.js version: ${process.version}`);
console.log(`Running in directory: ${process.cwd()}`);

// Get arguments from command line
const TOKEN_ADDRESS = process.argv[2] || 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263'; // Default to BONK
const AMOUNT_SOL = parseFloat(process.argv[3] || '0.005');
const IS_SELL = process.argv[4] === 'true'; // Third argument for sell operation

// Get environment variables
const RPC_URL = process.env.SOLANA_RPC_URL || process.env.solana_rpc_url || '';
const PRIVATE_KEY = process.env.WALLET_PRIVATE_KEY || '';

// Show environment variables are available (without revealing sensitive data)
console.log(`RPC_URL available: ${!!RPC_URL}`);
console.log(`PRIVATE_KEY available: ${!!PRIVATE_KEY}`);
console.log(`Operation: ${IS_SELL ? 'SELL' : 'BUY'}`);

// Retry function with exponential backoff
async function retryWithBackoff(fn, maxRetries = MAX_RETRIES, initialDelay = INITIAL_RETRY_DELAY) {
  let retries = 0;
  while (true) {
    try {
      return await fn();
    } catch (error) {
      if (error.response && error.response.status === 429) {
        retries++;
        if (retries > maxRetries) {
          console.error(`Failed after ${maxRetries} retries due to rate limiting`);
          throw error;
        }
        
        // Calculate exponential backoff with jitter
        const delay = initialDelay * Math.pow(2, retries - 1) * (1 + Math.random() * 0.2);
        console.log(`Rate limited (429). Retry ${retries}/${maxRetries} after ${Math.round(delay)}ms`);
        await new Promise(resolve => setTimeout(resolve, delay));
        continue;
      }
      throw error;
    }
  }
}

async function executeSwap() {
  try {
    console.log(`Starting ${IS_SELL ? 'sell' : 'buy'} for ${TOKEN_ADDRESS} with ${AMOUNT_SOL} SOL`);
    
    // Create connection to Solana with higher timeout
    const connection = new Connection(RPC_URL, 'confirmed');
    
    // Create keypair from private key
    const keypair = Keypair.fromSecretKey(bs58.decode(PRIVATE_KEY));
    console.log(`Using wallet public key: ${keypair.publicKey.toBase58()}`);
    
    // Convert SOL to lamports
    const amountLamports = Math.floor(AMOUNT_SOL * 1_000_000_000);
    
    // Use the public Jupiter API
    const JUPITER_API_BASE = 'https://quote-api.jup.ag';
    
    // Set input and output mints based on operation
    const inputMint = IS_SELL 
      ? TOKEN_ADDRESS 
      : "So11111111111111111111111111111111111111112"; // SOL mint address
    const outputMint = IS_SELL 
      ? "So11111111111111111111111111111111111111112" // SOL mint address 
      : TOKEN_ADDRESS;
    
    // For sell operations, we need to get token balance first to know how much to sell
    let amount = amountLamports;
    
    if (IS_SELL) {
      console.log("Getting token accounts to determine available balance...");
      
      try {
        // CRITICAL: Since we're finding zero balances, let's try a direct token supply query first
        // to check if the token even exists on the blockchain
        try {
          const tokenInfoResponse = await connection.getTokenSupply(new PublicKey(TOKEN_ADDRESS));
          console.log(`Token supply info:`, JSON.stringify(tokenInfoResponse.value, null, 2));
          // The decimals will be important for properly formatting the amount
        } catch (tokenInfoError) {
          console.log(`Token supply query failed, token may not exist: ${tokenInfoError.message}`);
          // If token doesn't exist, mark as sold
          if (tokenInfoError.message.includes("Invalid") || tokenInfoError.message.includes("not found")) {
            console.error("Token appears to be invalid. Marking as sold to remove from monitoring.");
            process.exit(0);  // Exit with 0 to treat as success for monitoring purposes
          }
        }
        
        // Continue with the token account lookup
        const tokenAccounts = await connection.getParsedTokenAccountsByOwner(
          keypair.publicKey,
          { mint: new PublicKey(TOKEN_ADDRESS) }
        );
        
        if (tokenAccounts.value.length === 0) {
          console.error(`No token accounts found for ${TOKEN_ADDRESS}`);
          
          // Try to look for the token with getTokenAccountsByOwner instead
          console.log("Trying alternative method to find token...");
          const allTokens = await connection.getTokenAccountsByOwner(
            keypair.publicKey,
            { programId: new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA') }
          );
          
          console.log(`Found ${allTokens.value.length} total token accounts`);
          
          // Log all tokens for debugging
          if (allTokens.value.length > 0) {
            console.log("Listing all token accounts:");
            for (let i = 0; i < allTokens.value.length; i++) {
              try {
                const accountInfo = await connection.getParsedAccountInfo(allTokens.value[i].pubkey);
                const mint = accountInfo.value?.data?.parsed?.info?.mint || "Unknown";
                const tokenAmount = accountInfo.value?.data?.parsed?.info?.tokenAmount?.amount || "0";
                console.log(`Token ${i+1}: Mint=${mint}, Amount=${tokenAmount}`);
              } catch (e) {
                console.log(`Error parsing token ${i+1}: ${e.message}`);
              }
            }
          }
          
          let foundTokenAccount = false;
          
          // Loop through all tokens to find our target token
          for (const tokenAccount of allTokens.value) {
            try {
              const accountInfo = await connection.getParsedAccountInfo(tokenAccount.pubkey);
              const parsedInfo = accountInfo.value?.data?.parsed?.info;
              
              if (parsedInfo && parsedInfo.mint === TOKEN_ADDRESS) {
                console.log(`Found token account for ${TOKEN_ADDRESS}`);
                const tokenBalance = parseInt(parsedInfo.tokenAmount.amount);
                console.log(`Found token balance: ${tokenBalance}`);
                
                // Force a minimum amount for sell operations
                if (tokenBalance === 0 || tokenBalance < 100) {
                  console.log("Token balance is zero or too small, marking as sold.");
                  process.exit(0);  // Exit with 0 to treat as success for monitoring purposes
                }
                
                amount = tokenBalance;
                foundTokenAccount = true;
                break;
              }
            } catch (err) {
              console.error(`Error checking token account: ${err.message}`);
            }
          }
          
          // If we still can't find the token, exit
          if (!foundTokenAccount) {
            console.error("Could not find token. Marking as sold anyway to remove from monitoring.");
            process.exit(0);  // Exit with 0 to treat as success for monitoring purposes
          }
        } else {
          // We found token accounts through the standard method
          // Log all accounts for debugging
          console.log(`Found ${tokenAccounts.value.length} token accounts for ${TOKEN_ADDRESS}`);
          
          let validTokenAccount = false;
          let largestBalance = 0;
          
          // Find the account with the largest balance
          for (const account of tokenAccounts.value) {
            const tokenBalance = parseInt(account.account.data.parsed.info.tokenAmount.amount);
            console.log(`Token account ${account.pubkey.toString()}: Balance=${tokenBalance}`);
            
            if (tokenBalance > largestBalance) {
              largestBalance = tokenBalance;
            }
          }
          
          // Get the balance of the account with the largest balance
          if (largestBalance > 0) {
            console.log(`Using largest balance: ${largestBalance}`);
            amount = largestBalance;
            validTokenAccount = true;
          } else {
            // All accounts have zero balance
            console.log("All token accounts have zero balance, marking as sold.");
            process.exit(0);  // Exit with 0 to treat as success for monitoring purposes
          }
          
          if (!validTokenAccount) {
            console.error("No valid token account found. Marking as sold.");
            process.exit(0);  // Exit with 0 to treat as success for monitoring purposes
          }
        }
      } catch (error) {
        console.error("Error checking token balance:", error.message);
        
        if (error.message.includes("Invalid public key input")) {
          console.error("Invalid token address. Marking as sold to remove from monitoring.");
          process.exit(0);  // Exit with 0 to treat as success for monitoring purposes
        }
        
        console.error("Could not find token. Marking as sold anyway to remove from monitoring.");
        process.exit(0);  // Exit with 0 to treat as success for monitoring purposes
      }
      
      // Final check that we have a valid amount to sell
      if (amount <= 0) {
        console.error("Amount to sell is zero or negative. Marking as sold.");
        process.exit(0);  // Exit with 0 to treat as success for monitoring purposes
      }
      
      console.log(`Final amount to sell: ${amount}`);
    }
    
    // Step 1: Get a quote with retry logic for rate limiting
    const quoteUrl = `${JUPITER_API_BASE}/v6/quote`;
    console.log(`Using quote URL: ${quoteUrl}`);
    
    const quoteParams = {
      inputMint: inputMint,
      outputMint: outputMint,
      amount: amount.toString(),
      slippageBps: IS_SELL ? "500" : "100"  // Even higher slippage for selling (5%)
    };
    
    console.log('Quote request params:', JSON.stringify(quoteParams, null, 2));
    
    // Use retryWithBackoff for quote request
    const quoteResponse = await retryWithBackoff(async () => {
      console.log('Attempting to get Jupiter quote...');
      return await axios.get(quoteUrl, { params: quoteParams });
    });
    
    if (!quoteResponse.data) {
      console.error('Failed to get quote', quoteResponse);
      if (IS_SELL) {
        console.error("Marking as sold anyway to remove from monitoring.");
        process.exit(0);  // Exit with 0 to treat as success for monitoring purposes
      }
      process.exit(1);
    }
    
    console.log(`Got Jupiter quote. Output amount: ${quoteResponse.data.outAmount}`);
    
    // Step 2: Get swap instructions with retry logic for rate limiting
    const swapUrl = `${JUPITER_API_BASE}/v6/swap`;
    console.log(`Using swap URL: ${swapUrl}`);
    
    // Fixed parameter conflict - use only prioritizationFeeLamports
    const swapRequest = {
      quoteResponse: quoteResponse.data,
      userPublicKey: keypair.publicKey.toBase58(),
      wrapUnwrapSOL: true,
      prioritizationFeeLamports: 300000, // 0.0003 SOL priority fee (increased)
      dynamicComputeUnitLimit: true
    };
    
    console.log('Swap request prepared');
    
    // Use retryWithBackoff for swap request
    const swapResponse = await retryWithBackoff(async () => {
      console.log('Attempting to prepare swap...');
      return await axios.post(swapUrl, swapRequest, {
        headers: { 'Content-Type': 'application/json' }
      });
    });
    
    if (!swapResponse.data || !swapResponse.data.swapTransaction) {
      console.error('Failed to get swap transaction', swapResponse.data);
      if (IS_SELL) {
        console.error("Marking as sold anyway to remove from monitoring.");
        process.exit(0);  // Exit with 0 to treat as success for monitoring purposes
      }
      process.exit(1);
    }
    
    // The transaction is already serialized from the API
    const serializedTx = swapResponse.data.swapTransaction;
    console.log('Received transaction data (length):', serializedTx.length);
    
    // For Versioned Transactions, we need to deserialize differently
    const buffer = Buffer.from(serializedTx, 'base64');
    const transaction = VersionedTransaction.deserialize(buffer);
    
    // Sign the transaction with our keypair
    transaction.sign([keypair]);
    
    // Submit the transaction
    console.log('Submitting transaction...');
    
    // Use sendRawTransaction with properly serialized, signed transaction
    const txSignature = await connection.sendRawTransaction(
      transaction.serialize(),
      {
        skipPreflight: false, // Run preflight checks to catch issues
        maxRetries: 5,
        preflightCommitment: 'processed'
      }
    );
    
    console.log('Transaction submitted:', txSignature);
    console.log(`View on Solscan: https://solscan.io/tx/${txSignature}`);
    
    // Just return success without waiting for confirmation
    console.log('SUCCESS', txSignature);
    process.exit(0);
  } catch (error) {
    console.error('Error executing swap:', error.message);
    if (error.response) {
      console.error('Response status:', error.response.status);
      console.error('Response data:', JSON.stringify(error.response.data, null, 2));
    }
    if (error.stack) {
      console.error('Stack trace:', error.stack);
    }
    
    // For sell operations, if there's an error, mark as sold to avoid infinite sell attempts
    if (IS_SELL) {
      console.error("Error during sell operation. Marking as sold anyway to remove from monitoring.");
      process.exit(0);  // Exit with 0 to treat as success for monitoring purposes
    }
    
    process.exit(1);
  }
}

// Run the function
executeSwap();
