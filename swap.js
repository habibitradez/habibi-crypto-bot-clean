const { Connection, Keypair, PublicKey, VersionedTransaction, TransactionInstruction, TransactionMessage, AddressLookupTableAccount } = require('@solana/web3.js');
const bs58 = require('bs58');
const axios = require('axios');
const fs = require('fs');

// Rate limiting constants
const MAX_RETRIES = 7;
const INITIAL_RETRY_DELAY = 3000;

// New slippage and priority fee constants
const MIN_SLIPPAGE_FOR_BUYS = 100;
const MIN_SLIPPAGE_FOR_SELLS = 500;
const MIN_SLIPPAGE_FOR_SMALL = 2000;
const MIN_PRIORITY_FEE_BUYS = 250000;
const MIN_PRIORITY_FEE_SELLS = 1500000;
const MIN_PRIORITY_FEE_SMALL = 3000000;

// QuickNode Metis configuration - UPDATED TO USE CORRECT ENVIRONMENT VARIABLES
const USE_QUICKNODE_METIS = process.env.USE_QUICKNODE_METIS === 'true';
const QUICKNODE_JUPITER_ENDPOINT = process.env.QUICKNODE_JUPITER_URL; // Your Jupiter API endpoint
const QUICKNODE_AUTH_TOKEN = process.env.QUICKNODE_AUTH_TOKEN; // Add this for authentication
const SOLANA_RPC_ENDPOINT = process.env.SOLANA_RPC_URL; // Your regular RPC endpoint
const QUICKNODE_RATE_LIMIT = 50; // 50 RPS for Launch plan
const QUICKNODE_API_DELAY = Math.floor(1000 / QUICKNODE_RATE_LIMIT); // 20ms between calls

// Global rate limiting for Jupiter API and QuickNode
let lastRequestTimestamps = [];
const MAX_REQUESTS_PER_MINUTE = USE_QUICKNODE_METIS ? QUICKNODE_RATE_LIMIT * 60 : 50;
let jupiterRateLimitResetTime = 0;
let lastQuickNodeCall = 0;

// RPC call rate limiting
let lastRpcCallTimestamps = {};
const RPC_RATE_LIMITS = {
  'default': { max: USE_QUICKNODE_METIS ? 45 : 40, windowMs: 60000 },
  'getTokenLargestAccounts': { max: 5, windowMs: 60000 },
  'getTokenSupply': { max: 10, windowMs: 60000 },
  'getParsedTokenAccountsByOwner': { max: 15, windowMs: 60000 },
  'getTokenAccountsByOwner': { max: 15, windowMs: 60000 },
  'getParsedAccountInfo': { max: 20, windowMs: 60000 }
};

// Print Node.js version for debugging
console.log(`Node.js version: ${process.version}`);
console.log(`Running in directory: ${process.cwd()}`);
console.log(`QuickNode Metis enabled: ${USE_QUICKNODE_METIS}`);
console.log(`QuickNode Jupiter URL: ${QUICKNODE_JUPITER_ENDPOINT ? 'Available' : 'Not set'}`);
console.log(`QuickNode Auth Token: ${QUICKNODE_AUTH_TOKEN ? 'Available' : 'Not set'}`);

// Helper function to get QuickNode headers with authentication
function getQuickNodeHeaders() {
  const headers = {
    'Content-Type': 'application/json',
    'User-Agent': 'SolanaBot/1.0',
    'Accept': 'application/json'
  };
  
  // Add authentication if token is available
  if (QUICKNODE_AUTH_TOKEN) {
    // Try common authentication header formats
    headers['Authorization'] = `Bearer ${QUICKNODE_AUTH_TOKEN}`;
    // Uncomment the line below if QuickNode uses x-api-key instead:
    // headers['x-api-key'] = QUICKNODE_AUTH_TOKEN;
  }
  
  return headers;
}
const TOKEN_ADDRESS = process.argv[2] || 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263';
const AMOUNT_SOL = parseFloat(process.argv[3] || '0.005');
const IS_SELL = process.argv[4] === 'true';
const IS_FORCE_SELL = process.argv[5] === 'true';

// Get environment variables
const RPC_URL = SOLANA_RPC_ENDPOINT || process.env.solana_rpc_url || '';
const PRIVATE_KEY = process.env.WALLET_PRIVATE_KEY || '';
const IS_SMALL_TOKEN_SELL = process.env.SMALL_TOKEN_SELL === 'true' && IS_SELL;

// Show environment variables are available
console.log(`RPC_URL available: ${!!RPC_URL}`);
console.log(`PRIVATE_KEY available: ${!!PRIVATE_KEY}`);
console.log(`Operation: ${IS_SELL ? 'SELL' : 'BUY'}`);
console.log(`Force sell: ${IS_FORCE_SELL ? 'YES' : 'NO'}`);
if (IS_SMALL_TOKEN_SELL) {
  console.log('Small token sell mode activated - using higher slippage and priority fees');
}

// Helper function to safely create PublicKey objects
function createPublicKey(address) {
  try {
    if (typeof address !== 'string') {
      throw new Error(`Address must be a string, got ${typeof address}`);
    }
    return new PublicKey(address);
  } catch (error) {
    console.error(`Error creating PublicKey from address: ${address}`, error.message);
    throw error;
  }
}

// QuickNode rate limiting function
async function quickNodeRateLimit() {
  if (!USE_QUICKNODE_METIS) return;
  
  const now = Date.now();
  const timeSinceLastCall = now - lastQuickNodeCall;
  
  if (timeSinceLastCall < QUICKNODE_API_DELAY) {
    const waitTime = QUICKNODE_API_DELAY - timeSinceLastCall;
    console.log(`QuickNode rate limiting: waiting ${waitTime}ms`);
    await new Promise(resolve => setTimeout(resolve, waitTime));
  }
  
  lastQuickNodeCall = Date.now();
}

// RPC throttling function
async function throttledRpcCall(connection, method, params) {
  const rateLimit = RPC_RATE_LIMITS[method] || RPC_RATE_LIMITS['default'];
  const now = Date.now();
  
  if (!lastRpcCallTimestamps[method]) {
    lastRpcCallTimestamps[method] = [];
  }
  
  lastRpcCallTimestamps[method] = lastRpcCallTimestamps[method].filter(
    timestamp => now - timestamp < rateLimit.windowMs
  );
  
  if (lastRpcCallTimestamps[method].length >= rateLimit.max) {
    const oldestCall = lastRpcCallTimestamps[method][0];
    const timeToWait = rateLimit.windowMs - (now - oldestCall) + 100;
    console.log(`Rate limiting ${method} RPC call. Waiting ${timeToWait}ms before proceeding.`);
    await new Promise(resolve => setTimeout(resolve, timeToWait));
    return throttledRpcCall(connection, method, params);
  }
  
  lastRpcCallTimestamps[method].push(now);
  await new Promise(resolve => setTimeout(resolve, USE_QUICKNODE_METIS ? 100 : 200));
  
  try {
    console.log(`Making throttled RPC call: ${method} (${lastRpcCallTimestamps[method].length}/${rateLimit.max})`);
    return await connection[method](...params);
  } catch (error) {
    if (error.message && error.message.includes('429')) {
      console.error(`RPC rate limit hit for ${method} despite throttling. Increasing backoff.`);
      if (rateLimit.max > 2) rateLimit.max--;
      await new Promise(resolve => setTimeout(resolve, 3000));
      return throttledRpcCall(connection, method, params);
    }
    throw error;
  }
}

// Improved rate limiting function
function canMakeRequest() {
  const now = Date.now();
  
  if (jupiterRateLimitResetTime > now) {
    const waitTime = jupiterRateLimitResetTime - now;
    console.log(`In Jupiter cooldown period. Waiting ${waitTime}ms`);
    return waitTime;
  }
  
  lastRequestTimestamps = lastRequestTimestamps.filter(time => now - time < 60000);
  
  if (lastRequestTimestamps.length < MAX_REQUESTS_PER_MINUTE) {
    lastRequestTimestamps.push(now);
    return true;
  }
  
  const timePerRequest = 60000 / MAX_REQUESTS_PER_MINUTE;
  const oldestTimestamp = lastRequestTimestamps[0];
  const idealNextTime = oldestTimestamp + timePerRequest;
  
  let timeToWait;
  if (idealNextTime > now) {
    timeToWait = idealNextTime - now + 50;
  } else {
    timeToWait = 1000;
  }
  
  console.log(`Distributing requests evenly. Waiting ${timeToWait}ms before next API call`);
  return timeToWait;
}

// Helper to log stats about our rate limiting
function logRateLimitStats() {
  const now = Date.now();
  lastRequestTimestamps = lastRequestTimestamps.filter(time => now - time < 60000);
  console.log(`Rate limit stats: ${lastRequestTimestamps.length}/${MAX_REQUESTS_PER_MINUTE} requests used in last minute`);
  if (jupiterRateLimitResetTime > now) {
    console.log(`Jupiter cooldown remaining: ${(jupiterRateLimitResetTime - now) / 1000}s`);
  }
}

// QuickNode Metis Jupiter API functions - FIXED TO USE CORRECT ENDPOINTS (NO /v6)
async function getQuoteViaQuickNode(inputMint, outputMint, amount, slippageBps) {
  await quickNodeRateLimit();
  
  console.log(`🔍 Getting quote via QuickNode Metis: ${amount} ${inputMint.slice(0,8)}... -> ${outputMint.slice(0,8)}...`);
  
  // FIXED: Use correct QuickNode Metis endpoint WITHOUT /v6 prefix
  const quoteUrl = `${QUICKNODE_JUPITER_ENDPOINT}/quote`;
  
  const params = {
    inputMint: inputMint,
    outputMint: outputMint,
    amount: amount.toString(),
    slippageBps: slippageBps.toString(),
    swapMode: 'ExactIn',
    onlyDirectRoutes: false,
    asLegacyTransaction: false
  };
  
  console.log(`QuickNode Jupiter Quote URL: ${quoteUrl}`);
  
  const response = await axios.get(quoteUrl, {
    params: params,
    timeout: 15000,
    headers: getQuickNodeHeaders()
  });
  
  if (response.data && response.data.outAmount) {
    console.log(`✅ QuickNode quote success: ${response.data.outAmount} tokens out`);
    return response;
  } else {
    throw new Error('Invalid quote response from QuickNode Metis');
  }
}

// QuickNode Metis approach using swap-instructions (following QuickNode guide)
async function getSwapInstructionsViaQuickNode(quoteResponse, userPublicKey, priorityFee) {
  await quickNodeRateLimit();
  
  console.log(`🔄 Getting swap instructions via QuickNode Metis (following QuickNode guide)...`);
  
  // Use /swap-instructions endpoint as recommended by QuickNode guide
  const swapInstructionsUrl = `${QUICKNODE_JUPITER_ENDPOINT}/swap-instructions`;
  
  const swapRequest = {
    userPublicKey: userPublicKey,
    quoteResponse: quoteResponse.data,
    wrapAndUnwrapSol: true,
    prioritizationFeeLamports: priorityFee,
    dynamicComputeUnitLimit: true,
    asLegacyTransaction: false
  };
  
  console.log(`QuickNode Jupiter Swap Instructions URL: ${swapInstructionsUrl}`);
  
  const response = await axios.post(swapInstructionsUrl, swapRequest, {
    timeout: 20000,
    headers: getQuickNodeHeaders()
  });
  
  if (response.data) {
    console.log(`✅ QuickNode swap instructions received`);
    return response;
  } else {
    throw new Error('Invalid swap instructions response from QuickNode Metis');
  }
}

// Fallback: Traditional swap endpoint for compatibility
async function getSwapTransactionViaQuickNode(quoteResponse, userPublicKey, priorityFee, slippageBps) {
  await quickNodeRateLimit();
  
  console.log(`🔄 Getting swap transaction via QuickNode Metis (traditional method)...`);
  
  // Try the traditional /swap endpoint
  const swapUrl = `${QUICKNODE_JUPITER_ENDPOINT}/swap`;
  
  const swapRequest = {
    userPublicKey: userPublicKey,
    quoteResponse: quoteResponse.data,
    wrapAndUnwrapSol: true,
    prioritizationFeeLamports: priorityFee,
    dynamicComputeUnitLimit: true,
    asLegacyTransaction: false
  };
  
  console.log(`QuickNode Jupiter Swap URL: ${swapUrl}`);
  
  const response = await axios.post(swapUrl, swapRequest, {
    timeout: 20000,
    headers: getQuickNodeHeaders()
  });
  
  if (response.data && response.data.swapTransaction) {
    console.log(`✅ QuickNode swap transaction received`);
    return response;
  } else {
    throw new Error('Invalid swap transaction response from QuickNode Metis');
  }
}

// Retry function with enhanced rate limiting
async function retryWithBackoff(fn, maxRetries = MAX_RETRIES, initialDelay = INITIAL_RETRY_DELAY) {
  let retries = 0;
  while (true) {
    try {
      logRateLimitStats();
      
      if (!USE_QUICKNODE_METIS) {
        const canProceed = canMakeRequest();
        if (canProceed !== true) {
          console.log(`Proactive rate limiting: Waiting ${canProceed}ms before attempting request`);
          await new Promise(resolve => setTimeout(resolve, canProceed));
          continue;
        }
      }
      
      return await fn();
      
    } catch (error) {
      if (error.response && error.response.status === 429) {
        console.error('Rate limited (429):', error.response.data);
        
        const resetHeader = error.response.headers['x-ratelimit-reset'] || 
                            error.response.headers['ratelimit-reset'];
        
        if (resetHeader) {
          const resetTime = parseInt(resetHeader) * 1000;
          jupiterRateLimitResetTime = Math.max(jupiterRateLimitResetTime, resetTime);
          console.log(`Rate limit will reset at: ${new Date(jupiterRateLimitResetTime).toISOString()}`);
        } else {
          jupiterRateLimitResetTime = Date.now() + (USE_QUICKNODE_METIS ? 10000 : 30000);
          console.log(`Setting cooldown for ${USE_QUICKNODE_METIS ? 10 : 30} seconds`);
        }
        
        retries++;
        if (retries > maxRetries) {
          console.error(`Failed after ${maxRetries} retries due to rate limiting`);
          throw error;
        }
        
        const delay = initialDelay * Math.pow(2, retries - 1) * (1 + Math.random() * 0.2);
        console.log(`Rate limited (429). Retry ${retries}/${maxRetries} after ${Math.round(delay)}ms`);
        await new Promise(resolve => setTimeout(resolve, delay));
        continue;
      }
      
      if (retries < maxRetries) {
        retries++;
        const delay = initialDelay * Math.pow(1.5, retries - 1);
        console.log(`Error: ${error.message}. Retry ${retries}/${maxRetries} after ${Math.round(delay)}ms`);
        await new Promise(resolve => setTimeout(resolve, delay));
        continue;
      }
      
      throw error;
    }
  }
}

async function executeSwap() {
  try {
    console.log(`Starting ${IS_SELL ? 'sell' : 'buy'} for ${TOKEN_ADDRESS} with ${AMOUNT_SOL} SOL${IS_FORCE_SELL ? ' (FORCE SELL MODE)' : ''}`);
    console.log(`Using ${USE_QUICKNODE_METIS ? 'QuickNode Metis Jupiter API' : 'Public Jupiter API'}`);
    
    if (USE_QUICKNODE_METIS && !QUICKNODE_JUPITER_ENDPOINT) {
      console.error('QuickNode Metis enabled but QUICKNODE_JUPITER_URL not set!');
      process.exit(1);
    }
    
    // Create connection to Solana with better error handling - USES REGULAR RPC
    const connection = new Connection(RPC_URL, {
      commitment: 'confirmed',
      disableRetryOnRateLimit: false,
      confirmTransactionInitialTimeout: 60000,
      httpHeaders: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36'
      }
    });
    
    // Create keypair from private key with better error handling
    let keypair;
    try {
      const secretKey = bs58.decode(PRIVATE_KEY);
      keypair = Keypair.fromSecretKey(secretKey);
      console.log(`Using wallet public key: ${keypair.publicKey.toBase58()}`);
    } catch (error) {
      console.error('Error creating keypair from private key:', error.message);
      process.exit(1);
    }
    
    // Convert SOL to lamports
    const amountLamports = Math.floor(AMOUNT_SOL * 1_000_000_000);
    
    // Use QuickNode Metis Jupiter or public Jupiter API
    const JUPITER_API_BASE = USE_QUICKNODE_METIS ? QUICKNODE_JUPITER_ENDPOINT : 'https://quote-api.jup.ag';
    
    // Set input and output mints based on operation
    const inputMint = IS_SELL 
      ? TOKEN_ADDRESS 
      : "So11111111111111111111111111111111111111112";
    const outputMint = IS_SELL 
      ? "So11111111111111111111111111111111111111112"
      : TOKEN_ADDRESS;
    
    // For sell operations, get token balance first
    let amount = amountLamports;
    let isVerySmallBalance = false;
    
    if (IS_SELL) {
      console.log("Getting token accounts to determine available balance...");
      
      try {
        // Check if token exists
        try {
          const tokenInfoResponse = await throttledRpcCall(connection, 'getTokenSupply', [createPublicKey(TOKEN_ADDRESS)]);
          console.log(`Token supply info:`, JSON.stringify(tokenInfoResponse.value, null, 2));
        } catch (tokenInfoError) {
          console.log(`Token supply query failed, token may not exist: ${tokenInfoError.message}`);
          if (tokenInfoError.message.includes("Invalid") || tokenInfoError.message.includes("not found")) {
            console.error("Token appears to be invalid. Marking as sold to remove from monitoring.");
            process.exit(0);
          }
        }
        
        // Get token accounts
        let tokenAccounts;
        try {
          tokenAccounts = await throttledRpcCall(connection, 'getParsedTokenAccountsByOwner', [
            keypair.publicKey,
            { mint: createPublicKey(TOKEN_ADDRESS) }
          ]);
        } catch (tokenError) {
          console.error(`Error getting token accounts: ${tokenError.message}`);
          
          if (IS_FORCE_SELL) {
            console.log("Force sell mode: Marking token as sold despite errors");
            process.exit(0);
          }
          
          throw tokenError;
        }
        
        if (!tokenAccounts || tokenAccounts.value.length === 0) {
          console.error(`No token accounts found for ${TOKEN_ADDRESS}`);
          
          if (IS_FORCE_SELL) {
            console.log("Force sell mode: No token accounts found, marking as sold");
            process.exit(0);
          }
          
          // Try alternative method
          console.log("Trying alternative method to find token...");
          try {
            const allTokens = await throttledRpcCall(connection, 'getTokenAccountsByOwner', [
              keypair.publicKey,
              { programId: createPublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA') }
            ]);
            
            console.log(`Found ${allTokens.value.length} total token accounts`);
            
            let foundTokenAccount = false;
            
            for (const tokenAccount of allTokens.value) {
              try {
                const accountInfo = await throttledRpcCall(connection, 'getParsedAccountInfo', [tokenAccount.pubkey]);
                const parsedInfo = accountInfo.value?.data?.parsed?.info;
                
                if (parsedInfo && parsedInfo.mint === TOKEN_ADDRESS) {
                  console.log(`Found token account for ${TOKEN_ADDRESS}`);
                  const tokenBalance = parseInt(parsedInfo.tokenAmount.amount);
                  console.log(`Found token balance: ${tokenBalance}`);
                  
                  if (tokenBalance < 1000) {
                    isVerySmallBalance = true;
                    console.log(`Very small balance detected (${tokenBalance}). Using aggressive sell parameters.`);
                  }
                  
                  if (tokenBalance === 0) {
                    console.log("Token balance is zero, marking as sold.");
                    process.exit(0);
                  }
                  
                  amount = tokenBalance;
                  foundTokenAccount = true;
                  break;
                }
              } catch (err) {
                console.error(`Error checking token account: ${err.message}`);
              }
            }
            
            if (!foundTokenAccount) {
              console.error("Could not find token. Marking as sold anyway to remove from monitoring.");
              process.exit(0);
            }
          } catch (error) {
            console.error(`Error getting all token accounts: ${error.message}`);
            console.error("Marking as sold to remove from monitoring.");
            process.exit(0);
          }
        } else {
          console.log(`Found ${tokenAccounts.value.length} token accounts for ${TOKEN_ADDRESS}`);
          
          let largestBalance = 0;
          
          for (const account of tokenAccounts.value) {
            const tokenBalance = parseInt(account.account.data.parsed.info.tokenAmount.amount);
            console.log(`Token account ${account.pubkey.toString()}: Balance=${tokenBalance}`);
            
            if (tokenBalance > largestBalance) {
              largestBalance = tokenBalance;
            }
          }
          
          if (largestBalance < 1000) {
            isVerySmallBalance = true;
            console.log(`Very small balance detected (${largestBalance}). Using aggressive sell parameters.`);
          }
          
          if (largestBalance > 0) {
            console.log(`Using largest balance: ${largestBalance}`);
            amount = largestBalance;
          } else {
            console.log("All token accounts have zero balance, marking as sold.");
            process.exit(0);
          }
        }
      } catch (error) {
        console.error("Error checking token balance:", error.message);
        
        if (error.message.includes("Invalid public key input")) {
          console.error("Invalid token address. Marking as sold to remove from monitoring.");
          process.exit(0);
        }
        
        console.error("Could not find token. Marking as sold anyway to remove from monitoring.");
        process.exit(0);
      }
      
      if (amount <= 0) {
        console.error("Amount to sell is zero or negative. Marking as sold.");
        process.exit(0);
      }
      
      console.log(`Final amount to sell: ${amount}`);
    }
    
    // Determine slippage
    let slippageBps;
    if (IS_SMALL_TOKEN_SELL || isVerySmallBalance || IS_FORCE_SELL) {
      slippageBps = MIN_SLIPPAGE_FOR_SMALL.toString();
      console.log(`Using ${parseInt(slippageBps)/100}% slippage for ${IS_FORCE_SELL ? 'force sell' : 'small token sell'}`);
    } else if (IS_SELL) {
      slippageBps = MIN_SLIPPAGE_FOR_SELLS.toString();
    } else {
      slippageBps = MIN_SLIPPAGE_FOR_BUYS.toString();
    }
    
    // Determine priority fee
    let priorityFee;
    if (IS_SMALL_TOKEN_SELL || isVerySmallBalance || IS_FORCE_SELL) {
      priorityFee = MIN_PRIORITY_FEE_SMALL;
      console.log(`Using very high priority fee (${priorityFee/1000000} SOL) for ${IS_FORCE_SELL ? 'force sell' : 'small token sell'}`);
    } else if (IS_SELL) {
      priorityFee = MIN_PRIORITY_FEE_SELLS;
    } else {
      priorityFee = MIN_PRIORITY_FEE_BUYS;
    }
    
    // Step 1: Get a quote (QuickNode Metis or public Jupiter)
    let quoteResponse;
    
    if (USE_QUICKNODE_METIS) {
      // Use QuickNode Metis Jupiter API for quotes only (since quotes work)
      console.log(`💎 Using QuickNode Metis Jupiter API for quotes (working)`);
      quoteResponse = await retryWithBackoff(async () => {
        return await getQuoteViaQuickNode(inputMint, outputMint, amount, parseInt(slippageBps));
      });
      
      console.log(`⚠️ Using public Jupiter API for swaps (QuickNode swap endpoints have 405 errors)`);
      // Fall through to use public Jupiter API for swaps
    } else {
      // Use public Jupiter API
      const quoteUrl = `${JUPITER_API_BASE}/v6/quote`;
      console.log(`Using public Jupiter quote URL: ${quoteUrl}`);
      
      const quoteParams = {
        inputMint: inputMint,
        outputMint: outputMint,
        amount: amount.toString(),
        slippageBps: slippageBps
      };
      
      console.log('Quote request params:', JSON.stringify(quoteParams, null, 2));
      
      quoteResponse = await retryWithBackoff(async () => {
        console.log('Attempting to get Jupiter quote...');
        return await axios.get(quoteUrl, { 
          params: quoteParams,
          headers: {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36'
          },
          timeout: 20000
        });
      });
    }
    
    if (!quoteResponse.data) {
      console.error('Failed to get quote', quoteResponse);
      if (IS_SELL || IS_FORCE_SELL) {
        console.error("Marking as sold anyway to remove from monitoring.");
        process.exit(0);
      }
      process.exit(1);
    }
    
    console.log(`Got Jupiter quote. Output amount: ${quoteResponse.data.outAmount}`);
    
    await new Promise(resolve => setTimeout(resolve, USE_QUICKNODE_METIS ? 500 : 1000));
    
    // Step 2: Get swap instructions (Use public Jupiter API for now due to QuickNode 405 errors)
    let swapResponse;
    
    // Always use public Jupiter API for swaps until QuickNode 405 errors are resolved
    console.log(`🔄 Using PUBLIC Jupiter API for swaps (QuickNode has 405 errors on swap endpoints)`);
    const swapUrl = `https://quote-api.jup.ag/v6/swap`;
    console.log(`Using public Jupiter swap URL: ${swapUrl}`);
    
    const swapRequest = {
      quoteResponse: quoteResponse.data,
      userPublicKey: keypair.publicKey.toBase58(),
      wrapAndUnwrapSol: true,
      prioritizationFeeLamports: priorityFee,
      dynamicComputeUnitLimit: true,
      dynamicSlippage: { maxBps: parseInt(slippageBps) }
    };
    
    console.log('Swap request prepared for public Jupiter API');
    
    const maxRetries = (IS_SMALL_TOKEN_SELL || isVerySmallBalance || IS_FORCE_SELL) ? 15 : 7;
    swapResponse = await retryWithBackoff(async () => {
      console.log('Attempting to prepare swap via public Jupiter API...');
      return await axios.post(swapUrl, swapRequest, {
        headers: { 
          'Content-Type': 'application/json',
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36'
        },
        timeout: 20000
      });
    }, maxRetries);
    
    // Handle public Jupiter API or if QuickNode didn't create a transaction above
    if (!transaction) {
      if (!swapResponse || !swapResponse.data || !swapResponse.data.swapTransaction) {
        console.error('No swap transaction available from any method');
        if (IS_SELL || IS_FORCE_SELL) {
          console.error("Marking as sold anyway to remove from monitoring.");
          process.exit(0);
        }
        process.exit(1);
      }
      
      // Handle traditional serialized transaction
      const serializedTx = swapResponse.data.swapTransaction;
      console.log('Received transaction data (length):', serializedTx.length);
      
      try {
        const buffer = Buffer.from(serializedTx, 'base64');
        transaction = VersionedTransaction.deserialize(buffer);
        console.log('Successfully deserialized VersionedTransaction');
        
        // Sign the transaction
        transaction.sign([keypair]);
        console.log('Transaction signed successfully');
      } catch (deserializeError) {
        console.error('Error deserializing/signing transaction:', deserializeError.message);
        if (IS_SELL || IS_FORCE_SELL) {
          console.error("Marking as sold anyway due to transaction error.");
          process.exit(0);
        }
        process.exit(1);
      }
    }
    
    // Submit the transaction
    console.log('Submitting transaction...');
    
    await new Promise(resolve => setTimeout(resolve, USE_QUICKNODE_METIS ? 500 : 1000));
    
    // Determine transaction parameters
    const txParams = {
      skipPreflight: (IS_SMALL_TOKEN_SELL || isVerySmallBalance || IS_FORCE_SELL) ? true : false,
      maxRetries: (IS_SMALL_TOKEN_SELL || isVerySmallBalance || IS_FORCE_SELL) ? 15 : 10,
      preflightCommitment: 'processed'
    };
    
    // Use sendRawTransaction with properly serialized, signed transaction
    const txSignature = await connection.sendRawTransaction(
      transaction.serialize(),
      txParams
    );
    
    console.log('Transaction submitted:', txSignature);
    console.log(`View on Solscan: https://solscan.io/tx/${txSignature}`);
    
    // Return success
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
    
    // For sell operations, mark as sold to avoid infinite attempts
    if (IS_SELL || IS_FORCE_SELL) {
      console.error(`Error during ${IS_FORCE_SELL ? 'force sell' : 'sell'} operation. Marking as sold anyway to remove from monitoring.`);
      process.exit(0);
    }
    
    process.exit(1);
  }
}

// Run the function
executeSwap();
