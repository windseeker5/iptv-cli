#!/usr/bin/env python3
"""
Direct test of show_vod_results to ensure it's working
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Now import the main module
from iptv import IPTVMenuManager

def test_direct_call():
    """Directly test the show_vod_results method"""
    
    print("Direct Test of show_vod_results")
    print("=" * 60)
    
    # Create instance
    manager = IPTVMenuManager()
    
    # Create test data matching Smart VOD Picks "Must Watch 9.0+"
    test_results = [
        {
            'name': 'NF - Filming The Wild Pear Tree',
            'stream_id': 1,
            'stream_url': 'http://test.com/1',
            'year': None,
            'rating': 10.0,
            'genre': None,
            'category_name': 'NETFLIX MOVIES'
        },
        {
            'name': 'EN - Changing Hearts  (2002)',
            'stream_id': 2,
            'stream_url': 'http://test.com/2',
            'year': None,
            'rating': 10.0,
            'genre': None,
            'category_name': 'EN - ROMANCE'
        },
        {
            'name': 'EN - Creed of Gold  (2014)',
            'stream_id': 3,
            'stream_url': 'http://test.com/3',
            'year': None,
            'rating': 10.0,
            'genre': None,
            'category_name': 'EN - ACTION'
        }
    ]
    
    print("\nTest data created with:")
    for r in test_results:
        print(f"  - {r['name'][:30]}: rating={r['rating']}, cat={r['category_name']}")
    
    print("\nNOTE: The show_vod_results method will display a menu.")
    print("Check if you see:")
    print("  1. DEBUG lines showing the data")
    print("  2. Formatted lines with ⭐ ratings, years, and categories")
    print("\nPress Ctrl+C to exit the menu when done checking.")
    print("-" * 60)
    
    try:
        # Call the actual method
        manager.show_vod_results(test_results, "TEST: Must Watch 9.0+")
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_direct_call()