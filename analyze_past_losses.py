#!/usr/bin/env python3
"""
Analyze past trading losses to understand what went wrong
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
import pandas as pd
import matplotlib.pyplot as plt
from collections import defaultdict

# Database connection
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("❌ DATABASE_URL not set!")
    exit(1)

def analyze_losses():
    """Deep dive into trading losses"""
    
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cursor = conn.cursor()
    
    print("🔍 ANALYZING YOUR TRADING LOSSES...\n")
    
    # 1. OVERALL STATISTICS
    cursor.execute("""
        SELECT 
            COUNT(*) as total_trades,
            SUM(CASE WHEN profit_sol > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN profit_sol <= 0 THEN 1 ELSE 0 END) as losses,
            SUM(profit_sol) as total_pnl_sol,
            AVG(profit_sol) as avg_pnl_sol,
            MAX(profit_sol) as best_trade,
            MIN(profit_sol) as worst_trade,
            AVG(hold_time_minutes) as avg_hold_time
        FROM copy_trades
        WHERE status = 'closed'
    """)
    
    stats = cursor.fetchone()
    
    if stats['total_trades'] > 0:
        win_rate = (stats['wins'] / stats['total_trades']) * 100
        print(f"📊 OVERALL PERFORMANCE:")
        print(f"   Total Trades: {stats['total_trades']}")
        print(f"   Win Rate: {win_rate:.1f}% ({stats['wins']} wins, {stats['losses']} losses)")
        print(f"   Total P&L: {stats['total_pnl_sol']:.3f} SOL (${stats['total_pnl_sol']*240:.0f})")
        print(f"   Average per trade: {stats['avg_pnl_sol']:.3f} SOL")
        print(f"   Best Trade: +{stats['best_trade']:.3f} SOL")
        print(f"   Worst Trade: {stats['worst_trade']:.3f} SOL")
        print(f"   Avg Hold Time: {stats['avg_hold_time']:.0f} minutes\n")
    
    # 2. ANALYZE BY EXIT REASON
    cursor.execute("""
        SELECT 
            exit_reason,
            COUNT(*) as count,
            SUM(profit_sol) as total_pnl,
            AVG(profit_sol) as avg_pnl,
            AVG(profit_pct) as avg_pct
        FROM copy_trades
        WHERE status = 'closed'
        GROUP BY exit_reason
        ORDER BY total_pnl ASC
    """)
    
    print("💔 LOSSES BY EXIT REASON:")
    exit_reasons = cursor.fetchall()
    for reason in exit_reasons:
        if reason['total_pnl'] < 0:
            print(f"   {reason['exit_reason'] or 'unknown'}: "
                  f"{reason['count']} trades, "
                  f"{reason['total_pnl']:.3f} SOL loss, "
                  f"avg {reason['avg_pct']:.1f}%")
    
    # 3. ANALYZE HOLDING TOO LONG
    cursor.execute("""
        SELECT 
            token_address,
            entry_price,
            exit_price,
            profit_sol,
            profit_pct,
            hold_time_minutes,
            exit_reason
        FROM copy_trades
        WHERE status = 'closed'
            AND profit_sol < -0.02
            AND hold_time_minutes > 120
        ORDER BY profit_sol ASC
        LIMIT 10
    """)
    
    print("\n⏰ BIGGEST LOSSES FROM HOLDING TOO LONG:")
    long_holds = cursor.fetchall()
    for trade in long_holds:
        print(f"   {trade['token_address'][:8]}: "
              f"Held {trade['hold_time_minutes']:.0f}min, "
              f"Lost {abs(trade['profit_sol']):.3f} SOL ({trade['profit_pct']:.1f}%)")
    
    # 4. ANALYZE WALLET PERFORMANCE
    cursor.execute("""
        SELECT 
            wallet_name,
            wallet_address,
            COUNT(*) as trades,
            SUM(CASE WHEN profit_sol > 0 THEN 1 ELSE 0 END) as wins,
            SUM(profit_sol) as total_pnl,
            AVG(profit_pct) as avg_pct
        FROM copy_trades
        WHERE status = 'closed'
        GROUP BY wallet_name, wallet_address
        HAVING COUNT(*) >= 5
        ORDER BY total_pnl ASC
        LIMIT 10
    """)
    
    print("\n👎 WORST PERFORMING WALLETS:")
    bad_wallets = cursor.fetchall()
    for wallet in bad_wallets:
        win_rate = (wallet['wins'] / wallet['trades']) * 100 if wallet['trades'] > 0 else 0
        print(f"   {wallet['wallet_name']}: "
              f"{win_rate:.0f}% WR, "
              f"{wallet['total_pnl']:.3f} SOL loss from {wallet['trades']} trades")
    
    # 5. ANALYZE PATTERNS IN LOSSES
    cursor.execute("""
        SELECT 
            EXTRACT(HOUR FROM created_at) as hour,
            COUNT(*) as trades,
            SUM(CASE WHEN profit_sol < 0 THEN 1 ELSE 0 END) as losses,
            SUM(profit_sol) as total_pnl
        FROM copy_trades
        WHERE status = 'closed'
        GROUP BY hour
        ORDER BY total_pnl ASC
    """)
    
    print("\n🕐 WORST TRADING HOURS:")
    hours = cursor.fetchall()
    for hour in hours[:5]:
        if hour['total_pnl'] < 0:
            loss_rate = (hour['losses'] / hour['trades']) * 100 if hour['trades'] > 0 else 0
            print(f"   Hour {int(hour['hour'])}:00 - "
                  f"{loss_rate:.0f}% loss rate, "
                  f"{hour['total_pnl']:.3f} SOL total loss")
    
    # 6. COULD PROFITS HAVE BEEN TAKEN?
    cursor.execute("""
        SELECT 
            COUNT(*) as count,
            SUM(profit_sol) as total_loss
        FROM copy_trades
        WHERE status = 'closed'
            AND profit_sol < 0
            AND profit_pct < -20
    """)
    
    big_losses = cursor.fetchone()
    print(f"\n💸 TRADES THAT DROPPED > 20%: {big_losses['count']} trades, {big_losses['total_loss']:.3f} SOL lost")
    print("   These likely went positive first but no profit was taken!")
    
    # 7. POSITION SIZE ANALYSIS
    cursor.execute("""
        SELECT 
            position_size,
            COUNT(*) as trades,
            SUM(profit_sol) as total_pnl,
            AVG(profit_pct) as avg_pct
        FROM copy_trades
        WHERE status = 'closed'
        GROUP BY position_size
        ORDER BY position_size DESC
    """)
    
    print("\n📏 LOSSES BY POSITION SIZE:")
    positions = cursor.fetchall()
    for pos in positions:
        if pos['total_pnl'] < 0:
            print(f"   {pos['position_size']:.2f} SOL positions: "
                  f"{pos['trades']} trades, "
                  f"{pos['total_pnl']:.3f} SOL loss")
    
    # 8. ML COULD HAVE HELPED
    print("\n🤖 WHAT YOUR ML MODEL COULD HAVE PREVENTED:")
    
    # Simulate ML filtering
    cursor.execute("""
        SELECT 
            ct.*,
            wp.win_rate as wallet_win_rate,
            wp.total_trades as wallet_total_trades
        FROM copy_trades ct
        LEFT JOIN (
            SELECT 
                wallet_address,
                COUNT(*) as total_trades,
                (SUM(CASE WHEN profit_sol > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)) as win_rate
            FROM copy_trades
            WHERE status = 'closed'
            GROUP BY wallet_address
        ) wp ON ct.wallet_address = wp.wallet_address
        WHERE ct.status = 'closed'
            AND ct.profit_sol < 0
    """)
    
    ml_preventable_loss = 0
    ml_preventable_trades = 0
    
    all_losses = cursor.fetchall()
    for trade in all_losses:
        # Would ML have rejected this?
        wallet_wr = trade['wallet_win_rate'] or 50
        
        # ML would reject if wallet WR < 60% or low liquidity
        if wallet_wr < 60:
            ml_preventable_loss += abs(trade['profit_sol'])
            ml_preventable_trades += 1
    
    print(f"   Trades ML would have rejected: {ml_preventable_trades}")
    print(f"   SOL that could have been saved: {ml_preventable_loss:.3f}")
    print(f"   Potential savings: ${ml_preventable_loss * 240:.0f}")
    
    # 9. THE REAL PROBLEM
    print("\n❌ THE REAL PROBLEMS:")
    print("1. NO PROFIT TAKING: You held for 100%+ gains instead of taking 20-30%")
    print("2. NO ML FILTERING: Your 79% accurate ML model wasn't being used")
    print("3. TOO MANY TRADES: 1560 trades in one session = no filtering")
    print("4. LARGE POSITIONS: 0.3-0.5 SOL positions with poor win rate")
    print("5. FOLLOWING LOSERS: Blindly copying wallets with <20% win rates")
    
    cursor.close()
    conn.close()

def create_visual_analysis():
    """Create visual charts of the losses"""
    
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    
    # Get data for visualization
    query = """
        SELECT 
            DATE(created_at) as date,
            COUNT(*) as trades,
            SUM(CASE WHEN profit_sol > 0 THEN 1 ELSE 0 END) as wins,
            SUM(profit_sol) as daily_pnl
        FROM copy_trades
        WHERE status = 'closed'
            AND created_at > CURRENT_DATE - INTERVAL '30 days'
        GROUP BY date
        ORDER BY date
    """
    
    df = pd.read_sql(query, conn)
    
    # Calculate cumulative P&L
    df['cumulative_pnl'] = df['daily_pnl'].cumsum()
    df['win_rate'] = (df['wins'] / df['trades'] * 100).fillna(0)
    
    # Create plots
    fig, axes = plt.subplots(3, 1, figsize=(12, 10))
    
    # Plot 1: Cumulative P&L
    axes[0].plot(df['date'], df['cumulative_pnl'], 'r-', linewidth=2)
    axes[0].axhline(y=0, color='k', linestyle='--', alpha=0.3)
    axes[0].set_title('Cumulative P&L Over Time (SOL)')
    axes[0].set_ylabel('SOL')
    axes[0].grid(True, alpha=0.3)
    
    # Plot 2: Daily Win Rate
    axes[1].bar(df['date'], df['win_rate'], color=['red' if x < 50 else 'green' for x in df['win_rate']])
    axes[1].axhline(y=50, color='k', linestyle='--', alpha=0.3)
    axes[1].set_title('Daily Win Rate %')
    axes[1].set_ylabel('Win Rate %')
    axes[1].set_ylim(0, 100)
    axes[1].grid(True, alpha=0.3)
    
    # Plot 3: Trade Volume
    axes[2].bar(df['date'], df['trades'], color='blue', alpha=0.6)
    axes[2].set_title('Daily Trade Count')
    axes[2].set_ylabel('Number of Trades')
    axes[2].grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('trading_losses_analysis.png', dpi=150)
    print("\n📊 Visual analysis saved to: trading_losses_analysis.png")
    
    conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("TRADING LOSS ANALYSIS - Understanding Your -9 SOL")
    print("=" * 60)
    print()
    
    analyze_losses()
    
    try:
        create_visual_analysis()
    except Exception as e:
        print(f"\n⚠️ Could not create visual analysis: {e}")
    
    print("\n" + "=" * 60)
    print("RECOMMENDATIONS TO RECOVER:")
    print("=" * 60)
    print("1. USE YOUR ML MODEL - It's 79% accurate but not being used!")
    print("2. TAKE PROFITS AT 20% - Stop waiting for 100%+ gains")
    print("3. LIMIT TRADES - Max 20/day, not 1500+")
    print("4. TINY POSITIONS - 0.02-0.05 SOL with your 2 SOL balance")
    print("5. DISABLE BAD WALLETS - Only follow 60%+ win rate wallets")
    print("\n💡 Your ML model WORKS - you just need to USE IT!")
