const { Connection, Keypair, PublicKey } = require('@solana/web3.js');
const bs58 = require('bs58');
const axios = require('axios');

// Print Node.js version for debugging
console.log(`Node.js version: ${process.version}`);
console.log(`Running in directory: ${process.cwd()}`);

// Get arguments from command line
const TOKEN_ADDRESS = process.argv[2] || 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263'; // Default to BONK
const AMOUNT_SOL = parseFloat(process.argv[3] || '0.005');

// Get environment variables - try multiple possible environment variable names
const RPC_URL = process.env.SOLANA_RPC_URL || process.env.solana_rpc_url || '';
const JUPITER_API_URL = process.env.JUPITER_API_URL || process.env.jupiter_api_url || process.env.QUICKNODE_API_URL || RPC_URL;
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
    
    // Check which API endpoint to use - try pump-fun/swap endpoint
    console.log('Getting swap transaction...');
    
    // Build the full URL for the swap endpoint
    let swapUrl = `${JUPITER_API_URL}/v6/swap`;
    
    // If the URL doesn't already contain /v6/swap, add /pump-fun/swap
    if (!JUPITER_API_URL.includes('/v6/swap')) {
      // Strip any trailing slash from JUPITER_API_URL
      const baseUrl = JUPITER_API_URL.endsWith('/') 
        ? JUPITER_API_URL.slice(0, -1) 
        : JUPITER_API_URL;
      swapUrl = `${baseUrl}/pump-fun/swap`;
    }
    
    console.log(`Using swap URL: ${swapUrl}`);
    
    const response = await axios.post(swapUrl, {
      wallet: keypair.publicKey.toBase58(),
      type: 'BUY',
      mint: TOKEN_ADDRESS,
      inAmount: amountLamports.toString(),
      priorityFeeLevel: 'high'
    }, {
      headers: {
        'Content-Type': 'application/json'
      }
    });
    
    if (!response.data || !response.data.transaction) {
      console.error('Failed to get swap transaction', response.data);
      process.exit(1);
    }
    
    // The transaction is already serialized from the API
    const serializedTx = response.data.transaction;
    
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
    if (!response.data || !response.data.transaction) {
      console.error('Failed to get swap transaction', response.data);
      process.exit(1);
    }
    
    // The transaction is already serialized from the API
    const serializedTx = response.data.transaction;
    
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
    process.exit(1);
  }
}

executeSwap();
