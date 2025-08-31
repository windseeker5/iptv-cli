#!/usr/bin/env python3
"""
Test API connections to debug download issues
"""
import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

def test_account_info():
    """Test account info API endpoint"""
    server = os.getenv('IPTV_SERVER_URL')
    username = os.getenv('IPTV_USERNAME') 
    password = os.getenv('IPTV_PASSWORD')
    
    print(f"Testing account info API...")
    print(f"Server: {server}")
    print(f"Username: {username}")
    print(f"Password: {password}")
    
    url = f"{server}/player_api.php?username={username}&password={password}"
    print(f"URL: {url}")
    
    try:
        response = requests.get(url, timeout=30)
        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            try:
                data = response.json()
                print("Response JSON:")
                print(json.dumps(data, indent=2))
                return True
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
                print(f"Raw response: {response.text[:500]}")
        else:
            print(f"HTTP Error: {response.status_code}")
            print(f"Response text: {response.text[:500]}")
            
    except requests.exceptions.RequestException as e:
        print(f"Request error: {e}")
        
    return False

def test_live_categories():
    """Test live categories API endpoint"""
    server = os.getenv('IPTV_SERVER_URL')
    username = os.getenv('IPTV_USERNAME') 
    password = os.getenv('IPTV_PASSWORD')
    
    print(f"\n\nTesting live categories API...")
    url = f"{server}/player_api.php?username={username}&password={password}&action=get_live_categories"
    print(f"URL: {url}")
    
    try:
        response = requests.get(url, timeout=30)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            try:
                data = response.json()
                print(f"Categories found: {len(data)}")
                if data:
                    print("First category:")
                    print(json.dumps(data[0], indent=2))
                return True
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
                print(f"Raw response: {response.text[:500]}")
        else:
            print(f"HTTP Error: {response.status_code}")
            print(f"Response text: {response.text[:500]}")
            
    except requests.exceptions.RequestException as e:
        print(f"Request error: {e}")
        
    return False

if __name__ == "__main__":
    print("=" * 50)
    print("IPTV API Connection Test")
    print("=" * 50)
    
    account_ok = test_account_info()
    categories_ok = test_live_categories()
    
    print("\n" + "=" * 50)
    print("RESULTS:")
    print(f"Account Info: {'✓' if account_ok else '✗'}")
    print(f"Live Categories: {'✓' if categories_ok else '✗'}")
    print("=" * 50)