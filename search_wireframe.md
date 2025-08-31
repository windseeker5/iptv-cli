# IPTV Search Interface Wireframe

Based on Claude Code's search interface style:

## Search Screen Layout

```
┌─────────────────────────────────────────────┐
│ ★ IPTV Search                               │
│ Database: iptv.db | 1,247 channels loaded  │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│ > Search channels: netflix_                 │
└─────────────────────────────────────────────┘

┌─── Search Results (5 matches) ──────────────┐
│ > Netflix Action Movies                     │
│   Category: Movies | Type: Live            │
│                                             │
│   Netflix Originals HD                     │
│   Category: Entertainment | Type: Live     │
│                                             │
│   Netflix Kids Zone                        │
│   Category: Kids | Type: VOD               │
│                                             │
│   Netflix Sports Network                   │
│   Category: Sports | Type: Live            │
│                                             │
│   Netflix Comedy Central                   │
│   Category: Comedy | Type: VOD             │
└─────────────────────────────────────────────┘

? Use ↑↓ arrows to select, Enter to play, Esc to search again
```

## Implementation Approach

```python
def search_interface():
    clear_screen()
    show_header("IPTV Search")
    
    while True:
        # Search input
        query = input("> Search channels: ")
        
        if not query:
            continue
            
        # Get results from database
        results = search_database(query)
        
        if results:
            show_search_results_card(results)
            selected = handle_arrow_selection(results)
            if selected:
                play_stream(selected)
        else:
            print("┌─── No Results Found ────────────────────────┐")
            print("│ Try different search terms                  │")
            print("└─────────────────────────────────────────────┘")

def show_search_results_card(results):
    count = len(results)
    print(f"┌─── Search Results ({count} matches) ──────────────┐")
    
    for i, result in enumerate(results):
        prefix = "> " if i == 0 else "  "
        print(f"│ {prefix}{result['name']:<40} │")
        print(f"│   Category: {result['category']} | Type: {result['type']:<8} │")
        print("│                                             │")
    
    print("└─────────────────────────────────────────────┘")
```

## Key Features:
- Card-style search results box (like Claude Code)
- Real-time search as you type
- Arrow key selection within results
- Clean box drawing characters
- Shows channel count and metadata
- Simple navigation hints at bottom

Would you like me to implement this search-focused interface?