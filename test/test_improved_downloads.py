#!/usr/bin/env python3
"""
Improved download functionality with better error handling and different protocols
"""
import requests
import json
import os
from dotenv import load_dotenv
import time

load_dotenv()

class IPTVTester:
    def __init__(self):
        self.server = os.getenv('IPTV_SERVER_URL')
        self.username = os.getenv('IPTV_USERNAME')
        self.password = os.getenv('IPTV_PASSWORD')
        
        # Try different server variations
        self.server_variants = [
            self.server,
            self.server.replace('http://', 'https://'),
            self.server + ':8080',
            self.server + ':80',
            self.server.replace('cf.', ''),  # Try without cf subdomain
        ]
        
    def test_server_variants(self):
        """Test different server URL variants"""
        print("Testing different server variants...")
        
        for variant in self.server_variants:
            print(f"\nTrying: {variant}")
            if self._test_single_server(variant):
                print(f"✓ SUCCESS with: {variant}")
                return variant
            
        print("✗ All server variants failed")
        return None
        
    def _test_single_server(self, server_url):
        """Test a single server URL"""
        try:
            url = f"{server_url}/player_api.php?username={self.username}&password={self.password}"
            
            # Try with different headers and user agents
            headers = {
                'User-Agent': 'VLC/3.0.0 LibVLC/3.0.0',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Connection': 'keep-alive',
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            print(f"  Status: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    if 'user_info' in data:
                        print(f"  ✓ Valid JSON response with user_info")
                        return True
                except:
                    pass
                    
        except Exception as e:
            print(f"  ✗ Error: {e}")
            
        return False
        
    def create_mock_data(self):
        """Create mock data for testing when server is unavailable"""
        print("\nServer unavailable, creating mock data for testing...")
        
        os.makedirs("data", exist_ok=True)
        
        # Mock account info
        mock_account = {
            "user_info": {
                "username": self.username,
                "password": self.password,
                "status": "Active",
                "exp_date": "1735689600",  # 2025-01-01
                "max_connections": "1"
            }
        }
        
        # Mock live categories
        mock_live_categories = [
            {"category_id": "1", "category_name": "Entertainment"},
            {"category_id": "2", "category_name": "News"},
            {"category_id": "3", "category_name": "Sports"}
        ]
        
        # Mock live streams
        mock_live_streams = [
            {
                "stream_id": "12345",
                "name": "Test Channel 1",
                "category_id": "1"
            },
            {
                "stream_id": "12346", 
                "name": "Test News Channel",
                "category_id": "2"
            }
        ]
        
        # Mock VOD categories
        mock_vod_categories = [
            {"category_id": "100", "category_name": "Movies"},
            {"category_id": "101", "category_name": "Series"}
        ]
        
        # Mock VOD streams
        mock_vod_streams = [
            {
                "stream_id": "20001",
                "name": "Test Movie 1",
                "category_id": "100",
                "year": "2023",
                "rating": "7.5",
                "genre": "Action",
                "container_extension": "mp4"
            }
        ]
        
        # Mock series categories
        mock_series_categories = [
            {"category_id": "200", "category_name": "Drama Series"}
        ]
        
        # Save mock data
        mock_files = {
            "data/account_info.json": mock_account,
            "data/live_categories.json": mock_live_categories,
            "data/live_streams.json": mock_live_streams,
            "data/vod_categories.json": mock_vod_categories,
            "data/vod_streams.json": mock_vod_streams,
            "data/series_categories.json": mock_series_categories
        }
        
        for filename, data in mock_files.items():
            with open(filename, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"✓ Created {filename}")
            
        return True

def main():
    print("=" * 60)
    print("IPTV Server Testing and Mock Data Creation")
    print("=" * 60)
    
    tester = IPTVTester()
    
    # Try to find working server
    working_server = tester.test_server_variants()
    
    if not working_server:
        print("\n" + "!" * 60)
        print("SERVER UNAVAILABLE - Creating mock data for testing")
        print("!" * 60)
        tester.create_mock_data()
        
        print("\n✓ Mock data created successfully!")
        print("You can now test the database creation functionality.")
        
    return working_server is not None

if __name__ == "__main__":
    main()