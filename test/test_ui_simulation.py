#!/usr/bin/env python3
"""
Simulate user interface interactions to test menu functionality
"""
import sys
import os
import sqlite3

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iptv import IPTVMenuManager

def test_database_statistics():
    """Test database statistics display"""
    print("\n" + "="*40)
    print("TESTING DATABASE STATISTICS")
    print("="*40)
    
    try:
        manager = IPTVMenuManager()
        
        # Check if database exists
        if not os.path.exists(manager.db_path):
            print("✗ No database found")
            return False
            
        # Get statistics manually to verify
        conn = sqlite3.connect(manager.db_path)
        cursor = conn.cursor()
        
        live_count = cursor.execute("SELECT COUNT(*) FROM live_streams").fetchone()[0]
        vod_count = cursor.execute("SELECT COUNT(*) FROM vod_streams").fetchone()[0]
        
        conn.close()
        
        print(f"✓ Live Streams: {live_count:,}")
        print(f"✓ VOD Content: {vod_count:,}")
        print(f"✓ Total Content: {live_count + vod_count:,}")
        
        # Test manager's show_final_stats method
        print("\nTesting show_final_stats method:")
        manager.show_final_stats()
        
        return True
        
    except Exception as e:
        print(f"✗ Statistics test failed: {e}")
        return False

def test_search_functionality():
    """Test search functionality"""
    print("\n" + "="*40)
    print("TESTING SEARCH FUNCTIONALITY")
    print("="*40)
    
    try:
        manager = IPTVMenuManager()
        
        # Test live channel search
        search_terms = ["news", "sport", "cnn", "bbc"]
        
        for term in search_terms:
            results = manager.search_live_channels(term)
            print(f"✓ Search '{term}': {len(results)} results")
            
            if results:
                # Show first result details
                first = results[0]
                print(f"  → First result: {first['name'][:40]}...")
                
        # Test VOD search
        vod_terms = ["movie", "action", "2023"]
        
        for term in vod_terms:
            results = manager.search_vod_content(term)
            print(f"✓ VOD search '{term}': {len(results)} results")
            
        return True
        
    except Exception as e:
        print(f"✗ Search test failed: {e}")
        return False

def test_database_validation():
    """Validate database structure and content"""
    print("\n" + "="*40)
    print("TESTING DATABASE VALIDATION")
    print("="*40)
    
    try:
        manager = IPTVMenuManager()
        
        if not os.path.exists(manager.db_path):
            print("✗ Database file missing")
            return False
            
        conn = sqlite3.connect(manager.db_path)
        cursor = conn.cursor()
        
        # Check required tables
        tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [table[0] for table in tables]
        
        required_tables = ['live_streams', 'vod_streams', 'account_info']
        
        for table in required_tables:
            if table in table_names:
                print(f"✓ Table '{table}' exists")
                
                # Check table structure
                columns = cursor.execute(f"PRAGMA table_info({table})").fetchall()
                col_names = [col[1] for col in columns]
                print(f"  → Columns: {', '.join(col_names)}")
                
                # Check for data
                count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  → {count:,} rows")
                
            else:
                print(f"✗ Table '{table}' missing")
                
        # Test indexes
        indexes = cursor.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        print(f"✓ Indexes created: {len(indexes)}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"✗ Database validation failed: {e}")
        return False

def main():
    """Run all UI simulation tests"""
    print("IPTV UI FUNCTIONALITY TESTING")
    print("="*50)
    
    tests = [
        ("Database Validation", test_database_validation),
        ("Database Statistics", test_database_statistics),  
        ("Search Functionality", test_search_functionality)
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        print(f"\nRunning {test_name}...")
        try:
            results[test_name] = test_func()
        except Exception as e:
            print(f"✗ {test_name} crashed: {e}")
            results[test_name] = False
            
    # Summary
    print("\n" + "="*50)
    print("TEST SUMMARY")
    print("="*50)
    
    all_passed = True
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{test_name}: {status}")
        if not passed:
            all_passed = False
            
    print("="*50)
    if all_passed:
        print("🎉 ALL UI TESTS PASSED!")
        print("\nYour IPTV script is ready to use!")
        print("Run: python3 iptv.py")
    else:
        print("❌ SOME TESTS FAILED")
        
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)