const { Connection, Keypair, PublicKey } = require('@solana/web3.js');
const bs58 = require('bs58');
const axios = require('axios');

// Get arguments from command line
const TOKEN_ADDRESS = process.argv[2] || 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263'; // Default to BONK
const AMOUNT_SOL = parseFloat(process.argv[3] || '0.005');

// Get environment variables
const RPC_URL = process.env.SOLANA_RPC_URL || '';
const QUICKNODE_API_URL = process.env.QUICKNODE_API_URL || '';
const PRIVATE_KEY = process.env.WALLET_PRIVATE_KEY || '';

async function executeSwap() {
  try {
    console.log(`Starting swap for ${TOKEN_ADDRESS} with ${AMOUNT_SOL} SOL`);
    
    // Create connection to Solana
    const connection = new Connection(RPC_URL, 'confirmed');
    
    // Create keypair from private key
    const keypair = Keypair.fromSecretKey(bs58.decode(PRIVATE_KEY));
    
    // Convert SOL to lamports
    const amountLamports = Math.floor(AMOUNT_SOL * 1_000_000_000);
    
    // Get swap transaction using QuickNode API
    console.log('Getting swap transaction...');
    const response = await axios.post(`${QUICKNODE_API_URL}/pump-fun/swap`, {
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
    process.exit(1);
  }
}

executeSwap();
