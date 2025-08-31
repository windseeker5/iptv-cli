#!/usr/bin/env python3
"""
Debug database creation specifically
"""
import sys
import os
import sqlite3
import json

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iptv import IPTVMenuManager

def test_database_creation():
    """Test database creation step by step"""
    print("="*50)
    print("DEBUGGING DATABASE CREATION")
    print("="*50)
    
    try:
        manager = IPTVMenuManager()
        
        # Check if JSON files exist
        json_files = [
            'data/account_info.json',
            'data/live_categories.json',
            'data/live_streams.json',
            'data/vod_categories.json',
            'data/vod_streams.json',
            'data/series_categories.json'
        ]
        
        print("Checking JSON files:")
        for file_path in json_files:
            if os.path.exists(file_path):
                size = os.path.getsize(file_path)
                print(f"✓ {file_path} ({size:,} bytes)")
            else:
                print(f"✗ {file_path} MISSING")
                
        # Test database creation directly
        print(f"\nTesting database creation...")
        print(f"Database path: {manager.db_path}")
        
        # Remove existing database if it exists
        if os.path.exists(manager.db_path):
            os.remove(manager.db_path)
            print("Removed existing database")
            
        # Call the database creation method directly
        success = manager._create_database()
        
        if success:
            print("✓ _create_database() returned success")
        else:
            print("✗ _create_database() returned failure")
            
        # Check if database file was created
        if os.path.exists(manager.db_path):
            size = os.path.getsize(manager.db_path)
            print(f"✓ Database file created: {size:,} bytes")
            
            # Test database content
            conn = sqlite3.connect(manager.db_path)
            cursor = conn.cursor()
            
            tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            print(f"✓ Tables created: {len(tables)}")
            
            for table in tables:
                table_name = table[0]
                count = cursor.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                print(f"  → {table_name}: {count:,} rows")
                
            conn.close()
        else:
            print("✗ Database file NOT created")
            
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        
def test_json_loading():
    """Test loading JSON data specifically"""
    print("\n" + "="*50)
    print("TESTING JSON DATA LOADING")
    print("="*50)
    
    try:
        # Test loading each JSON file
        json_files = {
            'data/account_info.json': 'account info',
            'data/live_categories.json': 'live categories',
            'data/live_streams.json': 'live streams',
            'data/vod_categories.json': 'vod categories',
            'data/vod_streams.json': 'vod streams',
            'data/series_categories.json': 'series categories'
        }
        
        for file_path, description in json_files.items():
            print(f"\nTesting {description}:")
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r') as f:
                        data = json.load(f)
                        
                    if isinstance(data, list):
                        print(f"  ✓ Valid JSON array with {len(data)} items")
                    elif isinstance(data, dict):
                        print(f"  ✓ Valid JSON object with keys: {list(data.keys())}")
                    else:
                        print(f"  ✓ Valid JSON data (type: {type(data)})")
                        
                except json.JSONDecodeError as e:
                    print(f"  ✗ JSON decode error: {e}")
                except Exception as e:
                    print(f"  ✗ Error reading file: {e}")
            else:
                print(f"  ✗ File not found: {file_path}")
                
    except Exception as e:
        print(f"✗ JSON loading test failed: {e}")

def main():
    """Main debug function"""
    test_json_loading()
    test_database_creation()

if __name__ == "__main__":
    main()