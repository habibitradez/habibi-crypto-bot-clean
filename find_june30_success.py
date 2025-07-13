import psycopg2
import os
from datetime import datetime

try:
    # Connect using your Render database
    conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
    cursor = conn.cursor()
    
    print("🔍 Connecting to Render database...")
    print("📊 Analyzing June 30th, 2024 trades...\n")
    
    # First, let's see what tables exist
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public';
    """)
    tables = cursor.fetchall()
    print("Available tables:")
    for table in tables:
        print(f"  - {table[0]}")
    
    # Try to find trades on June 30th
    # Common table names: trades, trade_history, transactions, etc.
    possible_tables = ['trades', 'trade_history', 'transactions', 'bot_trades']
    
    for table_name in possible_tables:
        try:
            query = f"""
            SELECT 
                token_address,
                entry_price,
                exit_price,
                position_size,
                pnl_sol,
                pnl_percentage,
                trade_open_time,
                strategy,
                exit_reason
            FROM {table_name}
            WHERE DATE(trade_open_time) = '2024-06-30'
                AND pnl_sol > 0
            ORDER BY pnl_sol DESC;
            """
            
            cursor.execute(query)
            results = cursor.fetchall()
            
            if results:
                print(f"\n🎯 FOUND JUNE 30TH TRADES in '{table_name}' table:")
                print("="*80)
                
                total_profit = 0
                for i, row in enumerate(results, 1):
                    token_addr, entry_price, exit_price, position_size, pnl_sol, pnl_pct, open_time, strategy, exit_reason = row
                    total_profit += pnl_sol
                    
                    print(f"Trade #{i}:")
                    print(f"  Token: {token_addr}")
                    print(f"  Entry: ${entry_price:.8f} | Exit: ${exit_price:.8f}")
                    print(f"  Position: {position_size} SOL")
                    print(f"  Profit: {pnl_sol:.4f} SOL ({pnl_pct:.1f}%)")
                    print(f"  Time: {open_time}")
                    print(f"  Strategy: {strategy}")
                    print(f"  Exit: {exit_reason}")
                    print("-" * 40)
                
                print(f"\n💰 TOTAL JUNE 30TH PROFIT: {total_profit:.4f} SOL")
                
                # Now get token characteristics for these winners
                print(f"\n🔍 ANALYZING TOKEN CHARACTERISTICS:")
                for row in results[:3]:  # Top 3 profitable trades
                    token_addr = row[0]
                    pnl_sol = row[4]
                    
                    # Try to find token metrics
                    try:
                        metrics_query = f"""
                        SELECT holder_count, liquidity_usd, volume_24h, token_age_minutes, market_cap
                        FROM token_metrics 
                        WHERE token_address = '{token_addr}'
                        LIMIT 1;
                        """
                        cursor.execute(metrics_query)
                        metrics = cursor.fetchone()
                        
                        if metrics:
                            holders, liquidity, volume, age, mcap = metrics
                            print(f"\nToken: {token_addr[:8]}... (Profit: {pnl_sol:.4f} SOL)")
                            print(f"  Holders: {holders}")
                            print(f"  Liquidity: ${liquidity:,.0f}")
                            print(f"  24h Volume: ${volume:,.0f}")
                            print(f"  Age: {age:.0f} minutes")
                            print(f"  Market Cap: ${mcap:,.0f}")
                        else:
                            print(f"\nToken: {token_addr[:8]}... (Profit: {pnl_sol:.4f} SOL)")
                            print("  No metrics data found")
                    except:
                        print(f"\nToken: {token_addr[:8]}... (Profit: {pnl_sol:.4f} SOL)")
                        print("  Metrics table not accessible")
                
                break  # Found the right table, stop searching
                
        except Exception as e:
            # Table doesn't exist or query failed, try next one
            continue
    
    if not any(results for results in []):
        print("\n❌ No June 30th trades found in any table")
        print("Let's check what dates we have data for:")
        
        for table_name in possible_tables:
            try:
                cursor.execute(f"""
                    SELECT DATE(trade_open_time) as trade_date, COUNT(*) 
                    FROM {table_name}
                    WHERE trade_open_time IS NOT NULL
                    GROUP BY DATE(trade_open_time)
                    ORDER BY trade_date DESC
                    LIMIT 10;
                """)
                dates = cursor.fetchall()
                
                if dates:
                    print(f"\nRecent dates in {table_name}:")
                    for date, count in dates:
                        print(f"  {date}: {count} trades")
                    break
            except:
                continue

except Exception as e:
    print(f"❌ Database connection failed: {e}")
    print("Make sure your DATABASE_URL environment variable is set correctly")

finally:
    if 'conn' in locals():
        conn.close()
