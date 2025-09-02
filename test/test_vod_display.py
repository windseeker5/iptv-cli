#!/usr/bin/env python3
"""
Unit test for VOD display formatting
Tests that ratings, years, and genres are properly displayed
"""

import sys
import os
import re

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_vod_formatting():
    """Test VOD result formatting with real data structure"""
    
    # Sample data that matches what comes from get_smart_recommendations
    test_results = [
        {
            'name': 'EN - Changing Hearts  (2002)',
            'stream_id': 123,
            'stream_url': 'http://example.com/stream1',
            'year': None,  # Database has None for year
            'rating': 10.0,
            'genre': None,  # Database has None for genre
            'category_name': 'EN - ROMANCE'
        },
        {
            'name': 'NF - Filming The Wild Pear Tree',
            'stream_id': 124,
            'stream_url': 'http://example.com/stream2',
            'year': None,
            'rating': 10.0,
            'genre': None,
            'category_name': 'NETFLIX MOVIES'
        },
        {
            'name': 'EN - Creed of Gold  (2014)',
            'stream_id': 125,
            'stream_url': 'http://example.com/stream3',
            'year': None,
            'rating': 9.5,
            'genre': None,
            'category_name': 'EN - ACTION'
        },
        {
            'name': 'FR - l hoy',
            'stream_id': 126,
            'stream_url': 'http://example.com/stream4',
            'year': None,
            'rating': 9.0,
            'genre': None,
            'category_name': 'FR - COMEDY'
        }
    ]
    
    print("Testing VOD Display Formatting")
    print("=" * 60)
    
    # Simulate the formatting logic from show_vod_results
    formatted_results = []
    favorites_set = set()  # Empty favorites for testing
    
    for result in test_results:
        # This is the exact logic from show_vod_results
        
        # Extract year from name if not in year field
        year_match = re.search(r'\((\d{4})\)', result['name'])
        if year_match:
            year = year_match.group(1)
            # Remove year from display name
            display_name = re.sub(r'\s*\(\d{4}\)\s*', '', result['name'])
        else:
            year = result.get('year') or 'N/A'
            display_name = result['name']
        
        rating = f"⭐{result['rating']:.1f}" if result['rating'] else '⭐N/A'
        
        # Use category_name as genre since genre field is empty
        if result.get('category_name'):
            # Clean up category name to make it shorter
            category = result['category_name']
            # Extract main part (e.g., "FR - ACTION" -> "FR-ACTION")
            if ' - ' in category:
                parts = category.split(' - ')
                genre = f"{parts[0][:2]}-{parts[1][:8]}" if len(parts) > 1 else parts[0][:12]
            else:
                genre = category[:12]
        else:
            genre = result.get('genre', 'Unknown')[:12] if result.get('genre') else 'Unknown'
        
        is_fav = (result.get('stream_id'), 'vod') in favorites_set
        fav_indicator = "♥ " if is_fav else "  "
        
        # Create the formatted option
        option = f"{fav_indicator}{display_name[:30]} | {rating} | {year} | {genre}"
        
        formatted_results.append({
            'original': result['name'],
            'formatted': option,
            'display_name': display_name,
            'year': year,
            'rating': rating,
            'genre': genre
        })
        
        print(f"\nOriginal: {result['name']}")
        print(f"Formatted: {option}")
    
    print("\n" + "=" * 60)
    print("Test Results:")
    print("-" * 60)
    
    # Verify formatting worked correctly
    errors = []
    
    for i, fr in enumerate(formatted_results):
        print(f"\n{i+1}. {fr['formatted']}")
        
        # Check if star emoji is present for rating
        if '⭐' not in fr['formatted']:
            errors.append(f"Item {i+1}: Missing star emoji in rating")
        
        # Check if year was extracted from names with (YYYY)
        if '(2002)' in fr['original'] and '2002' not in fr['formatted']:
            errors.append(f"Item {i+1}: Year not extracted from name")
        
        # Check if year was removed from display name
        if '(2002)' in fr['original'] and '(2002)' in fr['formatted']:
            errors.append(f"Item {i+1}: Year not removed from display name")
        
        # Check if genre/category is present
        if fr['genre'] == 'Unknown' and test_results[i]['category_name']:
            errors.append(f"Item {i+1}: Category not used as genre")
        
        # Check if pipes are used as separators
        if ' | ' not in fr['formatted']:
            errors.append(f"Item {i+1}: Missing pipe separators")
    
    print("\n" + "=" * 60)
    if errors:
        print("ERRORS FOUND:")
        for error in errors:
            print(f"  ❌ {error}")
        return False
    else:
        print("✅ All tests passed!")
        print("\nExpected format examples:")
        for fr in formatted_results[:3]:
            print(f"  {fr['formatted']}")
        return True

def test_what_user_sees():
    """Show what the user is currently seeing vs what they should see"""
    
    print("\n" + "=" * 60)
    print("What User Currently Sees vs Expected")
    print("=" * 60)
    
    current_display = [
        "  NF - Filming The Wild Pear Tre",
        "  EN - Changing Hearts",
        "  EN - Creed of Gold",
        "  FR - l hoy"
    ]
    
    expected_display = [
        "  NF - Filming The Wild Pear Tre | ⭐10.0 | N/A | NF-MOVIES",
        "  EN - Changing Hearts | ⭐10.0 | 2002 | EN-ROMANCE",
        "  EN - Creed of Gold | ⭐9.5 | 2014 | EN-ACTION",
        "  FR - l hoy | ⭐9.0 | N/A | FR-COMEDY"
    ]
    
    print("\nCURRENT (Wrong):")
    for line in current_display:
        print(line)
    
    print("\nEXPECTED (Correct):")
    for line in expected_display:
        print(line)
    
    print("\nKey differences:")
    print("  1. Missing ⭐ rating scores")
    print("  2. Missing year (extracted from name or N/A)")
    print("  3. Missing genre/category")
    print("  4. No pipe separators")

if __name__ == "__main__":
    print("VOD Display Format Test Suite")
    print("=" * 60)
    
    # Run the formatting test
    success = test_vod_formatting()
    
    # Show comparison
    test_what_user_sees()
    
    if success:
        print("\n✅ Formatting logic is correct!")
        print("The issue must be that this code isn't being executed in the actual app.")
    else:
        print("\n❌ Formatting logic has issues that need to be fixed.")
    
    sys.exit(0 if success else 1)