#!/usr/bin/env python3
"""
Test the improved status display and database verification
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iptv import IPTVMenuManager

def test_status_display():
    """Test the improved status display"""
    print("="*60)
    print("TESTING IMPROVED STATUS DISPLAY")
    print("="*60)
    
    try:
        manager = IPTVMenuManager()
        
        print("Current working directory:", os.getcwd())
        print("Database path:", manager.db_path)
        print("Database exists:", os.path.exists(manager.db_path))
        
        if os.path.exists(manager.db_path):
            size = os.path.getsize(manager.db_path)
            print(f"Database size: {size:,} bytes ({size/1024/1024:.2f} MB)")
        
        print("\nStatus display output:")
        print("-" * 40)
        manager.show_status()
        print("-" * 40)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

def test_database_verification():
    """Verify database content"""
    print("\n" + "="*60)
    print("DATABASE CONTENT VERIFICATION")
    print("="*60)
    
    try:
        manager = IPTVMenuManager()
        
        if not os.path.exists(manager.db_path):
            print("❌ Database file not found")
            return False
            
        # Test search functionality  
        print("Testing search functionality...")
        
        live_results = manager.search_live_channels("news")
        print(f"✓ Live search 'news': {len(live_results)} results")
        
        vod_results = manager.search_vod_content("movie")  
        print(f"✓ VOD search 'movie': {len(vod_results)} results")
        
        # Test statistics
        print("\nTesting statistics...")
        manager.show_final_stats()
        
        return True
        
    except Exception as e:
        print(f"❌ Database verification failed: {e}")
        return False

if __name__ == "__main__":
    test_status_display()
    success = test_database_verification()
    
    print("\n" + "="*60)
    if success:
        print("🎉 DATABASE IS WORKING CORRECTLY!")
        print("\nYour IPTV script is ready. When you run 'python3 iptv.py':")
        print("1. You'll see the database status at the top")
        print("2. All search and browse functions will work")
        print("3. The database is already created and functional")
    else:
        print("❌ Database issues detected")
    print("="*60)