#!/usr/bin/env python3
"""
Test the menu system by simulating the download and build database option
"""
import sys
import os
import shutil

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iptv import IPTVMenuManager

def cleanup_test_database():
    """Remove existing database for fresh test"""
    if os.path.exists('iptv.db'):
        os.remove('iptv.db')
        print("✓ Removed existing database")
    
    if os.path.exists('data'):
        shutil.rmtree('data')
        print("✓ Removed existing data folder")

def test_download_and_build_menu():
    """Test the download and build database menu option"""
    print("="*60)
    print("TESTING MENU OPTION: Download and Build Database")
    print("="*60)
    
    try:
        # Clean slate
        cleanup_test_database()
        
        # Create manager
        manager = IPTVMenuManager()
        print("✓ Created IPTVMenuManager")
        
        # Simulate the menu selection (option 0)
        print("\nSimulating: Download and Build Database menu selection...")
        
        # This is what happens when user selects option 0
        success = manager._download_and_create_database()
        
        if success:
            print("✓ Download and build process completed successfully")
            
            # Show the final statistics like the real menu does
            print("\nFinal Statistics:")
            manager.show_final_stats()
            
            # Verify all files created
            expected_files = [
                'iptv.db',
                'data/account_info.json',
                'data/live_categories.json', 
                'data/live_streams.json',
                'data/vod_categories.json',
                'data/vod_streams.json',
                'data/series_categories.json'
            ]
            
            print("\nFile Verification:")
            all_files_exist = True
            for file_path in expected_files:
                if os.path.exists(file_path):
                    size = os.path.getsize(file_path)
                    print(f"✓ {file_path} ({size:,} bytes)")
                else:
                    print(f"✗ {file_path} MISSING")
                    all_files_exist = False
                    
            return all_files_exist
            
        else:
            print("✗ Download and build process failed")
            return False
            
    except Exception as e:
        print(f"✗ Menu test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main test runner"""
    print("IPTV MENU SYSTEM TEST")
    print("This simulates selecting 'Download and Build Database' from the menu")
    print("="*60)
    
    success = test_download_and_build_menu()
    
    print("\n" + "="*60)
    if success:
        print("🎉 MENU TEST PASSED!")
        print("\nThe 'Download and Build Database' option is working correctly.")
        print("You can now run: python3 iptv.py")
        print("And select option 1 to download and build your IPTV database.")
    else:
        print("❌ MENU TEST FAILED!")
        print("There are still issues with the download and build process.")
    
    print("="*60)
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)