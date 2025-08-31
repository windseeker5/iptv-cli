#!/usr/bin/env python3
"""
Test the exact menu flow that the user would experience
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iptv import IPTVMenuManager

def test_exact_menu_flow():
    """Test exactly what happens when user selects menu option"""
    print("="*60)
    print("TESTING EXACT MENU FLOW")
    print("="*60)
    
    # Check current working directory
    print(f"Current working directory: {os.getcwd()}")
    
    # Remove existing database to test fresh creation
    if os.path.exists('iptv.db'):
        os.remove('iptv.db')
        print("Removed existing database for fresh test")
    
    try:
        # Create manager exactly like the main script does
        manager = IPTVMenuManager()
        print("✓ Created IPTVMenuManager")
        
        # Show what the main menu would show
        print(f"Database path: {manager.db_path}")
        print(f"Server: {manager.server}")
        print(f"Username: {manager.username}")
        
        # Check the download and build method exists
        if hasattr(manager, 'download_and_build_database'):
            print("✓ download_and_build_database method exists")
        else:
            print("✗ download_and_build_database method missing")
            
        # Test the exact menu method call
        print("\nSimulating menu option 0 selection...")
        print("Calling: manager.download_and_build_database()")
        
        # Simulate what happens in the main menu when option 0 is selected
        # This is exactly what the main_menu method calls
        manager.download_and_build_database()
        
        # Check what happened
        print(f"\nChecking results:")
        if os.path.exists('iptv.db'):
            size = os.path.getsize('iptv.db')
            print(f"✓ Database created: {size:,} bytes")
        else:
            print("✗ Database NOT created")
            
        # List all files in current directory
        print(f"\nFiles in current directory:")
        files = os.listdir('.')
        for file in sorted(files):
            if not file.startswith('.') and not file == '__pycache__':
                if os.path.isfile(file):
                    size = os.path.getsize(file)
                    print(f"  {file} ({size:,} bytes)")
                else:
                    print(f"  {file}/ (directory)")
                    
    except Exception as e:
        print(f"✗ Error in menu flow: {e}")
        import traceback
        traceback.print_exc()

def test_method_calls():
    """Test individual method calls"""
    print("\n" + "="*60)
    print("TESTING INDIVIDUAL METHOD CALLS")
    print("="*60)
    
    try:
        manager = IPTVMenuManager()
        
        # Test _download_and_create_database method
        print("Testing _download_and_create_database()...")
        success = manager._download_and_create_database()
        print(f"Result: {success}")
        
        if os.path.exists('iptv.db'):
            size = os.path.getsize('iptv.db')
            print(f"✓ Database file exists: {size:,} bytes")
        else:
            print("✗ Database file not created")
            
    except Exception as e:
        print(f"✗ Error in method calls: {e}")

if __name__ == "__main__":
    test_exact_menu_flow()
    test_method_calls()