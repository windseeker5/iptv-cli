#!/usr/bin/env python3
"""
Master test runner - runs all tests to validate IPTV functionality
"""
import subprocess
import sys
import os

def run_test(test_script, description):
    """Run a test script and return the result"""
    print(f"\n{'='*60}")
    print(f"RUNNING: {description}")
    print(f"Script: {test_script}")
    print(f"{'='*60}")
    
    try:
        result = subprocess.run([sys.executable, test_script], 
                              capture_output=False, 
                              check=True)
        print(f"✅ {description}: PASSED")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description}: FAILED (exit code {e.returncode})")
        return False
    except Exception as e:
        print(f"❌ {description}: ERROR ({e})")
        return False

def main():
    """Run all tests in sequence"""
    print("IPTV COMPLETE TEST SUITE")
    print("="*80)
    print("This will run all tests to validate your IPTV script is working correctly.")
    print("="*80)
    
    # Test scripts in order of dependency
    tests = [
        ("test/test_api_connection.py", "API Connection Test"),
        ("test/test_improved_downloads.py", "Server Variants Test"), 
        ("test/test_full_workflow.py", "Complete Workflow Test"),
        ("test/test_ui_simulation.py", "UI Functionality Test"),
        ("test/test_menu_simulation.py", "Menu System Test")
    ]
    
    results = {}
    
    for script, description in tests:
        if not os.path.exists(script):
            print(f"❌ {description}: Test script not found: {script}")
            results[description] = False
            continue
            
        results[description] = run_test(script, description)
    
    # Final summary
    print("\n" + "="*80)
    print("FINAL TEST RESULTS SUMMARY")
    print("="*80)
    
    passed = 0
    failed = 0
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} {test_name}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print(f"\nTOTAL: {passed} passed, {failed} failed")
    print("="*80)
    
    if failed == 0:
        print("🎉 ALL TESTS PASSED!")
        print("\nYour IPTV script is fully functional and ready to use!")
        print("\nTo use your IPTV script:")
        print("1. Run: python3 iptv.py")  
        print("2. Select option 1: 'Download and Build Database'")
        print("3. Wait for download and database creation to complete")
        print("4. Use the other menu options to search and browse content")
        print("\nEnjoy your IPTV database manager! 🚀")
        return True
    else:
        print(f"❌ {failed} TEST(S) FAILED!")
        print("\nSome functionality may not work correctly.")
        print("Review the test output above for details.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)