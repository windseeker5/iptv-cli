#!/usr/bin/env python3
"""
Utility functions for IPTV application
Includes image processing utilities for logo and media handling
"""

import os
from PIL import Image
import numpy as np


def remove_background(input_path, output_path, color_to_remove='black', threshold=30):
    """
    Remove background from an image and save as transparent PNG
    
    Args:
        input_path (str): Path to input image
        output_path (str): Path to save transparent PNG
        color_to_remove (str): Color to remove ('black', 'white', or RGB tuple)
        threshold (int): Tolerance threshold for color matching (0-255)
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Open the image
        img = Image.open(input_path)
        
        # Convert to RGBA if not already
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # Get image data as numpy array
        data = np.array(img)
        
        # Define the color to remove
        if color_to_remove == 'black':
            target_color = (0, 0, 0)
        elif color_to_remove == 'white':
            target_color = (255, 255, 255)
        elif isinstance(color_to_remove, tuple) and len(color_to_remove) == 3:
            target_color = color_to_remove
        else:
            raise ValueError("color_to_remove must be 'black', 'white', or RGB tuple")
        
        # Create mask for pixels to make transparent
        mask = np.all(np.abs(data[:, :, :3] - target_color) <= threshold, axis=2)
        
        # Set alpha channel to 0 for masked pixels
        data[mask, 3] = 0
        
        # Create new image from modified data
        new_img = Image.fromarray(data, 'RGBA')
        
        # Save as PNG
        new_img.save(output_path, 'PNG')
        print(f"✓ Transparent image saved to: {output_path}")
        return True
        
    except Exception as e:
        print(f"✗ Error removing background: {e}")
        return False


def remove_background_advanced(input_path, output_path):
    """
    Advanced background removal using rembg (AI-based)
    Requires: pip install rembg
    
    Args:
        input_path (str): Path to input image
        output_path (str): Path to save transparent PNG
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        from rembg import remove
        
        # Open input image
        with open(input_path, 'rb') as input_file:
            input_data = input_file.read()
        
        # Remove background
        output_data = remove(input_data)
        
        # Save output
        with open(output_path, 'wb') as output_file:
            output_file.write(output_data)
        
        print(f"✓ Advanced background removal complete: {output_path}")
        return True
        
    except ImportError:
        print("✗ rembg not installed. Install with: pip install rembg")
        print("  Falling back to basic background removal...")
        return remove_background(input_path, output_path)
    except Exception as e:
        print(f"✗ Error with advanced removal: {e}")
        print("  Falling back to basic background removal...")
        return remove_background(input_path, output_path)


def process_logo(input_path, output_path, method='advanced'):
    """
    Process logo image to create transparent version
    
    Args:
        input_path (str): Path to input logo
        output_path (str): Path to save transparent logo
        method (str): 'basic' or 'advanced' background removal
    
    Returns:
        bool: True if successful
    """
    if not os.path.exists(input_path):
        print(f"✗ Input file not found: {input_path}")
        return False
    
    print(f"Processing logo: {input_path}")
    
    if method == 'advanced':
        success = remove_background_advanced(input_path, output_path)
    else:
        success = remove_background(input_path, output_path, 'black', threshold=40)
    
    if success:
        # Optionally resize if needed
        try:
            img = Image.open(output_path)
            # Keep aspect ratio, set max height to 60px for dashboard
            if img.height > 60:
                ratio = 60 / img.height
                new_width = int(img.width * ratio)
                img = img.resize((new_width, 60), Image.Resampling.LANCZOS)
                img.save(output_path)
                print(f"✓ Logo resized to {new_width}x60 pixels")
        except Exception as e:
            print(f"⚠ Could not resize: {e}")
    
    return success


def clean_edges(image_path, output_path=None):
    """
    Clean up edges of transparent image to remove any fringing
    
    Args:
        image_path (str): Path to transparent PNG
        output_path (str): Output path (defaults to overwriting input)
    
    Returns:
        bool: True if successful
    """
    try:
        if output_path is None:
            output_path = image_path
        
        img = Image.open(image_path)
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        data = np.array(img)
        
        # Remove semi-transparent black pixels at edges
        # These often appear as dark halos
        alpha = data[:, :, 3]
        
        # Where alpha is semi-transparent (not fully opaque or transparent)
        semi_transparent = (alpha > 10) & (alpha < 240)
        
        # Check if those pixels are dark
        is_dark = np.all(data[:, :, :3] < 50, axis=2)
        
        # Make dark semi-transparent pixels fully transparent
        data[semi_transparent & is_dark, 3] = 0
        
        # Save cleaned image
        new_img = Image.fromarray(data, 'RGBA')
        new_img.save(output_path, 'PNG')
        
        print(f"✓ Edges cleaned: {output_path}")
        return True
        
    except Exception as e:
        print(f"✗ Error cleaning edges: {e}")
        return False


if __name__ == "__main__":
    # Example usage
    print("IPTV Utility - Background Removal Tool")
    print("=" * 40)
    
    # Process the KDC logo
    input_logo = "/home/kdresdell/Documents/DEV/iptv/nginx/html/kdc-logo.png"
    output_logo = "/home/kdresdell/Documents/DEV/iptv/nginx/html/kdc-logo-transparent.png"
    
    if os.path.exists(input_logo):
        print(f"\nProcessing KDC logo...")
        # Try advanced method first, falls back to basic if rembg not installed
        success = process_logo(input_logo, output_logo, method='advanced')
        
        if success:
            # Clean edges for better quality
            clean_edges(output_logo)
            print(f"\n✓ Logo processing complete!")
            print(f"  Original: {input_logo}")
            print(f"  Transparent: {output_logo}")
    else:
        print(f"\n⚠ Logo file not found at: {input_logo}")
        print("  Please ensure the logo file exists before running.")