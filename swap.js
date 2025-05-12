const { Connection, Keypair, PublicKey, VersionedTransaction } = require('@solana/web3.js');
const bs58 = require('bs58');
const axios = require('axios');

// Rate limiting constants
const MAX_RETRIES = 5;
const INITIAL_RETRY_DELAY = 2000; // 2 seconds

// Global rate limiting
let lastRequestTimestamps = [];
const MAX_REQUESTS_PER_MINUTE = 55; // Set lower than the actual limit of 60

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
// Check if this is a small token sell operation
const IS_SMALL_TOKEN_SELL = process.env.SMALL_TOKEN_SELL === 'true' && IS_SELL;

// Show environment variables are available (without revealing sensitive data)
console.log(`RPC_URL available: ${!!RPC_URL}`);
console.log(`PRIVATE_KEY available: ${!!PRIVATE_KEY}`);
console.log(`Operation: ${IS_SELL ? 'SELL' : 'BUY'}`);
if (IS_SMALL_TOKEN_SELL) {
  console.log('Small token sell mode activated - using higher slippage and priority fees');
}

// Rate limiting function
function canMakeRequest() {
  const now = Date.now();
  // Remove timestamps older than 1 minute
  lastRequestTimestamps = lastRequestTimestamps.filter(time => now - time < 60000);
  
  // Check if we're under the limit
  if (lastRequestTimestamps.length < MAX_REQUESTS_PER_MINUTE) {
    // We can make a request
    lastRequestTimestamps.push(now);
    return true;
  }
  
  // Calculate wait time until we can make another request
  const oldestTimestamp = lastRequestTimestamps[0];
  const timeToWait = 60000 - (now - oldestTimestamp) + 100; // Add 100ms buffer
  return timeToWait; // Return wait time instead of false
}

// Retry function with enhanced rate limiting
async function retryWithBackoff(fn, maxRetries = MAX_RETRIES, initialDelay = INITIAL_RETRY_DELAY) {
  let retries = 0;
  while (true) {
    try {
      // Check if we can make a request based on our rate limiting
      const canProceed = canMakeRequest();
      if (canProceed !== true) {
        // We need to wait
        console.log(`Proactive rate limiting: Waiting ${canProceed}ms before attempting request`);
        await new Promise(resolve => setTimeout(resolve, canProceed));
        continue; // Try again after waiting
      }
      
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
    const connection = new Connection(RPC_URL, {
      commitment: 'confirmed',
      disableRetryOnRateLimit: false,
      httpHeaders: {
        'Content-Type': 'application/json',
      }
    });
    
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
    let isVerySmallBalance = false;
    
    if (IS_SELL) {
      console.log("Getting token accounts to determine available balance...");
      
      try {
        // Add delay before RPC calls to avoid rate limiting
        await new Promise(resolve => setTimeout(resolve, 100));
        
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
        
        // Add delay before next RPC call
        await new Promise(resolve => setTimeout(resolve, 100));
        
        // Continue with the token account lookup
        const tokenAccounts = await connection.getParsedTokenAccountsByOwner(
          keypair.publicKey,
          { mint: new PublicKey(TOKEN_ADDRESS) }
        );
        
        if (tokenAccounts.value.length === 0) {
          console.error(`No token accounts found for ${TOKEN_ADDRESS}`);
          
          // Try to look for the token with getTokenAccountsByOwner instead
          console.log("Trying alternative method to find token...");
          try {
            // Add delay before RPC call
            await new Promise(resolve => setTimeout(resolve, 100));
            
            const allTokens = await connection.getTokenAccountsByOwner(
              keypair.publicKey,
              { programId: new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA') }
            );
            
            console.log(`Found ${allTokens.value.length} total token accounts`);
            
            // Log all tokens for debugging
            if (allTokens.value.length > 0) {
              console.log("Listing all token accounts:");
              for (let i = 0; i < Math.min(allTokens.value.length, 5); i++) { // Reduced from 10 to 5 to limit RPC calls
                try {
                  // Add delay between account checks
                  if (i > 0) await new Promise(resolve => setTimeout(resolve, 100));
                  
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
                // Add delay between account checks
                await new Promise(resolve => setTimeout(resolve, 100));
                
                const accountInfo = await connection.getParsedAccountInfo(tokenAccount.pubkey);
                const parsedInfo = accountInfo.value?.data?.parsed?.info;
                
                if (parsedInfo && parsedInfo.mint === TOKEN_ADDRESS) {
                  console.log(`Found token account for ${TOKEN_ADDRESS}`);
                  const tokenBalance = parseInt(parsedInfo.tokenAmount.amount);
                  console.log(`Found token balance: ${tokenBalance}`);
                  
                  // Check if this is a very small balance
                  if (tokenBalance < 1000) {
                    isVerySmallBalance = true;
                    console.log(`Very small balance detected (${tokenBalance}). Using aggressive sell parameters.`);
                  }
                  
                  // Force a minimum amount for sell operations
                  if (tokenBalance === 0) {
                    console.log("Token balance is zero, marking as sold.");
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
          } catch (error) {
            console.error(`Error getting all token accounts: ${error.message}`);
            console.error("Marking as sold to remove from monitoring.");
            process.exit(0);
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
          
          // Check if this is a very small balance
          if (largestBalance < 1000) {
            isVerySmallBalance = true;
            console.log(`Very small balance detected (${largestBalance}). Using aggressive sell parameters.`);
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
    
    // Determine slippage based on operation type and token size
    let slippageBps;
    if (IS_SMALL_TOKEN_SELL || isVerySmallBalance) {
      slippageBps = "1000";  // 10% slippage for small tokens
      console.log("Using 10% slippage for small token sell");
    } else if (IS_SELL) {
      slippageBps = "500";   // 5% slippage for normal sells
    } else {
      slippageBps = "100";   // 1% slippage for buys
    }
    
    const quoteParams = {
      inputMint: inputMint,
      outputMint: outputMint,
      amount: amount.toString(),
      slippageBps: slippageBps
    };
    
    console.log('Quote request params:', JSON.stringify(quoteParams, null, 2));
    
    // Use retryWithBackoff for quote request
    const quoteResponse = await retryWithBackoff(async () => {
      console.log('Attempting to get Jupiter quote...');
      return await axios.get(quoteUrl, { 
        params: quoteParams,
        headers: {
          'Content-Type': 'application/json',
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
      });
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
    
    // Add a small delay between API calls
    await new Promise(resolve => setTimeout(resolve, 500));
    
    // Step 2: Get swap instructions with retry logic for rate limiting
    const swapUrl = `${JUPITER_API_BASE}/v6/swap`;
    console.log(`Using swap URL: ${swapUrl}`);
    
    // Determine priority fee based on operation type and token size
    let priorityFee;
    if (IS_SMALL_TOKEN_SELL || isVerySmallBalance) {
      priorityFee = 1000000;  // 0.001 SOL priority fee for small tokens (very high)
      console.log("Using high priority fee for small token sell");
    } else if (IS_SELL) {
      priorityFee = 500000;   // 0.0005 SOL for normal sells
    } else {
      priorityFee = 100000;   // 0.0001 SOL for buys
    }
    
    // Fixed parameter conflict - use only prioritizationFeeLamports
    const swapRequest = {
      quoteResponse: quoteResponse.data,
      userPublicKey: keypair.publicKey.toBase58(),
      wrapUnwrapSOL: true,
      prioritizationFeeLamports: priorityFee,
      dynamicComputeUnitLimit: true
    };
    
    console.log('Swap request prepared');
    
    // Use retryWithBackoff for swap request with more retries for small tokens
    const maxRetries = (IS_SMALL_TOKEN_SELL || isVerySmallBalance) ? 10 : 5;
    const swapResponse = await retryWithBackoff(async () => {
      console.log('Attempting to prepare swap...');
      return await axios.post(swapUrl, swapRequest, {
        headers: { 
          'Content-Type': 'application/json',
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
      });
    }, maxRetries);
    
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
    
    // Add a delay before submitting transaction
    await new Promise(resolve => setTimeout(resolve, 500));
    
    // Determine transaction parameters based on token type
    const txParams = {
      skipPreflight: (IS_SMALL_TOKEN_SELL || isVerySmallBalance) ? true : false,
      maxRetries: (IS_SMALL_TOKEN_SELL || isVerySmallBalance) ? 10 : 5,
      preflightCommitment: 'processed'
    };
    
    // Use sendRawTransaction with properly serialized, signed transaction
    const txSignature = await connection.sendRawTransaction(
      transaction.serialize(),
      txParams
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
