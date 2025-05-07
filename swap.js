vconst { Connection, Keypair, PublicKey, Transaction, sendAndConfirmTransaction, SystemProgram } = require('@solana/web3.js');
const bs58 = require('bs58');
const axios = require('axios');

// Print Node.js version for debugging
console.log(`Node.js version: ${process.version}`);
console.log(`Running in directory: ${process.cwd()}`);

// Get arguments from command line
const TOKEN_ADDRESS = process.argv[2] || 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263'; // Default to BONK
const AMOUNT_SOL = parseFloat(process.argv[3] || '0.005');

// Get environment variables
const RPC_URL = process.env.SOLANA_RPC_URL || process.env.solana_rpc_url || '';
const PRIVATE_KEY = process.env.WALLET_PRIVATE_KEY || '';

// Show environment variables are available (without revealing sensitive data)
console.log(`RPC_URL available: ${!!RPC_URL}`);
console.log(`PRIVATE_KEY available: ${!!PRIVATE_KEY}`);

async function executeSwap() {
  try {
    console.log(`Starting swap for ${TOKEN_ADDRESS} with ${AMOUNT_SOL} SOL`);
    
    // Create connection to Solana with higher timeout and extra confirmations
    const connection = new Connection(RPC_URL, {
      commitment: 'confirmed',
      confirmTransactionInitialTimeout: 60000 // 60 seconds
    });
    
    // Create keypair from private key
    const keypair = Keypair.fromSecretKey(bs58.decode(PRIVATE_KEY));
    console.log(`Using wallet public key: ${keypair.publicKey.toBase58()}`);
    
    // Convert SOL to lamports
    const amountLamports = Math.floor(AMOUNT_SOL * 1_000_000_000);
    
    // Use the public Jupiter API
    const JUPITER_API_BASE = 'https://quote-api.jup.ag';
    
    // Step 1: Get a quote
    const quoteUrl = `${JUPITER_API_BASE}/v6/quote`;
    console.log(`Using quote URL: ${quoteUrl}`);
    
    const quoteParams = {
      inputMint: "So11111111111111111111111111111111111111112", // SOL mint address
      outputMint: TOKEN_ADDRESS,
      amount: amountLamports.toString(),
      slippageBps: "100"
    };
    
    console.log('Quote request params:', JSON.stringify(quoteParams, null, 2));
    
    const quoteResponse = await axios.get(quoteUrl, { params: quoteParams });
    
    if (!quoteResponse.data) {
      console.error('Failed to get quote', quoteResponse);
      process.exit(1);
    }
    
    console.log(`Got quote with output amount: ${quoteResponse.data.outAmount}`);
    
    // Step 2: Get swap instructions
    const swapUrl = `${JUPITER_API_BASE}/v6/swap`;
    console.log(`Using swap URL: ${swapUrl}`);
    
    const swapRequest = {
      quoteResponse: quoteResponse.data,
      userPublicKey: keypair.publicKey.toBase58(),
      wrapUnwrapSOL: true,
      computeUnitPriceMicroLamports: 20000, // Increased priority fee for faster processing
      dynamicComputeUnitLimit: true
    };
    
    console.log('Swap request prepared');
    
    const swapResponse = await axios.post(swapUrl, swapRequest, {
      headers: { 'Content-Type': 'application/json' }
    });
    
    if (!swapResponse.data || !swapResponse.data.swapTransaction) {
      console.error('Failed to get swap transaction', swapResponse.data);
      process.exit(1);
    }
    
    // The transaction is already serialized from the API
    const serializedTx = swapResponse.data.swapTransaction;
    console.log('Received transaction data (length):', serializedTx.length);
    
    // Deserialize the transaction
    const txBuffer = Buffer.from(serializedTx, 'base64');
    
    // Get latest blockhash for transaction finality
    const {blockhash, lastValidBlockHeight} = await connection.getLatestBlockhash('finalized');
    
    // Create transaction from the buffer
    const transaction = Transaction.from(txBuffer);
    
    // Update the transaction with the latest blockhash
    transaction.recentBlockhash = blockhash;
    transaction.lastValidBlockHeight = lastValidBlockHeight;
    
    // Set fee payer to ensure proper signing
    transaction.feePayer = keypair.publicKey;
    
    // Clear existing signatures if any (important to avoid signature verification failures)
    transaction.signatures = [];
    
    // Now sign the transaction with the keypair
    const signedTx = transaction.sign([keypair]);
    
    // Submit the transaction
    console.log('Submitting transaction...');
    
    // Use sendRawTransaction with properly serialized, signed transaction
    const txSignature = await connection.sendRawTransaction(
      signedTx.serialize(),
      {
        skipPreflight: false, // Run preflight checks to catch issues
        maxRetries: 5,
        preflightCommitment: 'processed'
      }
    );
    
    console.log('Transaction submitted:', txSignature);
    console.log(`View on Solscan: https://solscan.io/tx/${txSignature}`);
    
    console.log('Waiting for confirmation...');
    
    // Wait for confirmation with increased timeout
    try {
      const confirmation = await connection.confirmTransaction({
        signature: txSignature,
        blockhash,
        lastValidBlockHeight
      }, 'confirmed');
      
      if (confirmation.value.err) {
        console.error('Transaction confirmed but has error:', confirmation.value.err);
      } else {
        console.log('Transaction confirmed successfully!');
      }
    } catch (confirmError) {
      console.log('Confirmation timeout or error. Transaction might still go through:', confirmError.message);
    }
    
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
    process.exit(1);
  }
}

// Run the function
executeSwap();
