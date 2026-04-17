#!/usr/bin/env python3
"""
Convert XML duplicate project file (.dupproj) to CSV format.

This script parses a Cisdem Duplicate Finder XML file and extracts
duplicate file groups, converting them to a simple CSV format with
group numbers and file paths.
"""

import csv
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


def clean_file_path(file_path):
    """
    Clean file path by removing the _simlar_{number} suffix.

    Args:
        file_path (str): Original file path that may contain _simlar_{number} suffix

    Returns:
        str: Cleaned file path without the _simlar_{number} suffix
    """
    if not file_path:
        return file_path

    # Pattern to match _simlar_ followed by 1-3 digits at the end of the filename
    # This handles cases like: img_1234.jpg_simlar_100 -> img_1234.jpg
    # or img_1234_simlar_100 -> img_1234
    pattern = r"_simlar_\d{1,3}(?=\.|$)"

    # Replace the pattern with empty string
    cleaned_path = re.sub(pattern, "", file_path)

    return cleaned_path


def parse_dupproj_to_csv(xml_file_path, csv_file_path):
    """
    Convert .dupproj XML file to CSV format.

    Args:
        xml_file_path (str): Path to the input .dupproj XML file
        csv_file_path (str): Path to the output CSV file
    """
    try:
        # Parse the XML file
        tree = ET.parse(xml_file_path)
        root = tree.getroot()

        # Find all GroupItem elements
        group_items = root.findall(".//GroupItem")

        print(f"Found {len(group_items)} duplicate groups")

        # Prepare CSV data
        csv_data = []

        # Process each group
        for group_num, group_item in enumerate(group_items, start=1):
            # Find all Item elements within this group
            items = group_item.findall("Item")

            for item in items:
                raw_file_path = item.text.strip() if item.text else ""
                if raw_file_path:  # Only add non-empty paths
                    # Convert to lowercase first (as per sample requirement)
                    file_path = raw_file_path.lower()

                    # Clean the file path by removing _simlar_{number} suffix
                    file_path = clean_file_path(file_path)

                    # Extract folder path and file name
                    path_obj = Path(file_path)
                    folder_path = str(path_obj.parent) + "\\"

                    csv_data.append(
                        {
                            "GroupNumber": group_num,
                            "IsMark": 0,  # Default value from sample
                            "IsLocked": 0,  # Default value from sample
                            "FolderPath": folder_path,
                            "FilePath": file_path,  # Already lowercase
                            "Capture Date": "",  # Not available in XML
                            "Modified Date": "",  # Not available in XML
                            "FileSize": "",  # Not available in XML
                        }
                    )

        # Write to CSV
        fieldnames = [
            "GroupNumber",
            "IsMark",
            "IsLocked",
            "FolderPath",
            "FilePath",
            "Capture Date",
            "Modified Date",
            "FileSize",
        ]

        with open(csv_file_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_data)

        print(f"Successfully converted {len(csv_data)} file entries to {csv_file_path}")
        print(f"Created {len(group_items)} duplicate groups")

    except ET.ParseError as e:
        print(f"Error parsing XML file: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: File '{xml_file_path}' not found")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


def main():
    """Main function to handle command line arguments and run conversion."""
    if len(sys.argv) != 3:
        print("Usage: python convert_dupproj_to_csv.py <input.dupproj> <output.csv>")
        print("Example: python convert_dupproj_to_csv.py testdata/20250916.dupproj output.csv")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    if not os.path.exists(input_file):
        print(f"Error: Input file '{input_file}' does not exist")
        sys.exit(1)

    parse_dupproj_to_csv(input_file, output_file)


if __name__ == "__main__":
    main()
