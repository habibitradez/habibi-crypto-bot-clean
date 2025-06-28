import psycopg2
import os



try:
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS wallet_status (
            wallet_address VARCHAR(100) PRIMARY KEY,
            is_active BOOLEAN DEFAULT TRUE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    print("✅ Table created successfully!")
    
    # Verify it exists
    cursor.execute("SELECT * FROM wallet_status LIMIT 1")
    print("✅ Table verified - ready to use!")
    
    cursor.close()
    conn.close()
    
except Exception as e:
    print(f"Error: {e}")
