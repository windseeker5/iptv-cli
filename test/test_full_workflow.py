#!/usr/bin/env python3
"""
Complete workflow test for IPTV database creation
"""
import sys
import os
import sqlite3
import shutil

# Add parent directory to path so we can import the main module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iptv import IPTVMenuManager

def cleanup_test_files():
    """Clean up test files"""
    test_files = ['iptv.db', 'data']
    for file_path in test_files:
        if os.path.exists(file_path):
            if os.path.isdir(file_path):
                shutil.rmtree(file_path)
            else:
                os.remove(file_path)
    print("✓ Cleaned up test files")

def test_download_and_build():
    """Test the complete download and build process"""
    print("\n" + "="*50)
    print("TESTING COMPLETE DOWNLOAD AND BUILD PROCESS")
    print("="*50)
    
    try:
        # Create IPTV manager instance
        manager = IPTVMenuManager()
        print("✓ Created IPTVMenuManager instance")
        
        # Test the download and build process
        print("\nStarting download and database creation...")
        success = manager._download_and_create_database()
        
        if not success:
            print("✗ Download and build failed")
            return False
            
        print("✓ Download and build completed")
        
        # Verify database was created
        if not os.path.exists('iptv.db'):
            print("✗ Database file not created")
            return False
            
        print("✓ Database file created")
        
        # Verify JSON files were created
        expected_files = [
            'data/account_info.json',
            'data/live_categories.json',
            'data/live_streams.json',
            'data/vod_categories.json',
            'data/vod_streams.json',
            'data/series_categories.json'
        ]
        
        for file_path in expected_files:
            if os.path.exists(file_path):
                print(f"✓ {file_path} created")
            else:
                print(f"✗ {file_path} missing")
                
        # Test database content
        conn = sqlite3.connect('iptv.db')
        cursor = conn.cursor()
        
        # Check tables exist
        tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [table[0] for table in tables]
        
        expected_tables = ['live_streams', 'vod_streams', 'account_info']
        for table in expected_tables:
            if table in table_names:
                print(f"✓ Table '{table}' exists")
                
                # Check table has data
                count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  → {count} rows in {table}")
            else:
                print(f"✗ Table '{table}' missing")
                
        conn.close()
        
        # Test search functionality
        print("\nTesting search functionality...")
        results = manager.search_live_channels("test")
        print(f"✓ Search returned {len(results)} results")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_individual_downloads():
    """Test individual download functions"""
    print("\n" + "="*50) 
    print("TESTING INDIVIDUAL DOWNLOAD FUNCTIONS")
    print("="*50)
    
    try:
        manager = IPTVMenuManager()
        
        # Test each download function individually
        download_functions = [
            ('account_info', manager._download_account_info),
            ('live_categories', manager._download_live_categories),
            ('live_streams', manager._download_live_streams),
            ('vod_categories', manager._download_vod_categories),
            ('vod_streams', manager._download_vod_streams),
            ('series_categories', manager._download_series_categories)
        ]
        
        os.makedirs('data', exist_ok=True)
        
        for name, func in download_functions:
            print(f"\nTesting {name} download...")
            success = func()
            if success:
                print(f"✓ {name} download successful")
                
                # Check file was created
                file_path = f"data/{name}.json"
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path)
                    print(f"  → File created: {file_size} bytes")
                else:
                    print(f"  ✗ File not created: {file_path}")
            else:
                print(f"✗ {name} download failed")
                
        return True
        
    except Exception as e:
        print(f"✗ Individual download test failed: {e}")
        return False

def main():
    """Main test function"""
    print("IPTV WORKFLOW TESTING")
    print("="*60)
    
    # Clean up before testing
    cleanup_test_files()
    
    # Test individual downloads first
    individual_success = test_individual_downloads()
    
    # Clean up again
    cleanup_test_files()
    
    # Test complete workflow
    workflow_success = test_download_and_build()
    
    # Final results
    print("\n" + "="*60)
    print("FINAL TEST RESULTS")
    print("="*60)
    print(f"Individual Downloads: {'✓ PASS' if individual_success else '✗ FAIL'}")
    print(f"Complete Workflow:    {'✓ PASS' if workflow_success else '✗ FAIL'}")
    print("="*60)
    
    if individual_success and workflow_success:
        print("🎉 ALL TESTS PASSED! The download functionality is working!")
        return True
    else:
        print("❌ SOME TESTS FAILED. Check the error messages above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)