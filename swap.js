const { Connection, Keypair, PublicKey } = require('@solana/web3.js');
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
const JUPITER_API_URL = process.env.JUPITER_API_URL || process.env.jupiter_api_url || '';
const PRIVATE_KEY = process.env.WALLET_PRIVATE_KEY || '';

// Show environment variables are available (without revealing sensitive data)
console.log(`RPC_URL available: ${!!RPC_URL}`);
console.log(`JUPITER_API_URL available: ${!!JUPITER_API_URL}`);
console.log(`PRIVATE_KEY available: ${!!PRIVATE_KEY}`);

async function executeSwap() {
  try {
    console.log(`Starting swap for ${TOKEN_ADDRESS} with ${AMOUNT_SOL} SOL`);
    
    // Create connection to Solana
    const connection = new Connection(RPC_URL, 'confirmed');
    
    // Create keypair from private key
    const keypair = Keypair.fromSecretKey(bs58.decode(PRIVATE_KEY));
    console.log(`Using wallet public key: ${keypair.publicKey.toBase58()}`);
    
    // Convert SOL to lamports
    const amountLamports = Math.floor(AMOUNT_SOL * 1_000_000_000);
    
    // For QuickNode's Metis Jupiter API, the URL structure is different
    // It should be the base URL like https://metis.quiknode.pro/your-api-key/
    
    // First check if we're using the swap-api URL (which is incorrect)
    let baseUrl = JUPITER_API_URL;
    if (baseUrl.includes('jupiter-swap-api.quiknode.pro')) {
      // Extract the API key and reconstruct the URL
      const apiKeyMatch = baseUrl.match(/\/([A-Za-z0-9]+)\/?$/);
      if (apiKeyMatch && apiKeyMatch[1]) {
        baseUrl = `https://metis.quiknode.pro/${apiKeyMatch[1]}/`;
      }
    }
    
    // Ensure it ends with a slash
    if (!baseUrl.endsWith('/')) {
      baseUrl += '/';
    }
    
    // Use the correct endpoints
    const quoteUrl = `${baseUrl}v6/quote`;
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
    
    // Now use the quote to prepare a swap transaction
    const swapUrl = `${baseUrl}v6/swap`;
    console.log(`Using swap URL: ${swapUrl}`);
    
    const swapRequest = {
      quoteResponse: quoteResponse.data,
      userPublicKey: keypair.publicKey.toBase58(),
      wrapUnwrapSOL: true,
      computeUnitPriceMicroLamports: 1000, // Priority fee
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
    
    // Submit the transaction
    console.log('Submitting transaction...');
    const txSignature = await connection.sendRawTransaction(
      Buffer.from(serializedTx, 'base64'),
      {
        skipPreflight: true,
        maxRetries: 5,
        preflightCommitment: 'processed'
      }
    );
    
    console.log('Transaction submitted:', txSignature);
    console.log(`View on Solscan: https://solscan.io/tx/${txSignature}`);
    
    // Wait for confirmation
    console.log('Waiting for confirmation...');
    const confirmation = await connection.confirmTransaction(txSignature, 'confirmed');
    
    if (confirmation.value.err) {
      console.error('Transaction failed:', confirmation.value.err);
      process.exit(1);
    }
    
    console.log('Transaction confirmed successfully!');
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
