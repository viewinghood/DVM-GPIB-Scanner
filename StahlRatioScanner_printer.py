"""
Created on Mon Dec 01 15:33:01 2025

@author: Heming

file:
    StahlRatioScanner_printer.py

Ratio measurement plotter for Keithley 2100 - CSV Data Plotter
Reads CSV data and creates the same plot as the live measurement
"""

import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import csv
import os
from matplotlib.ticker import MultipleLocator, FuncFormatter, Locator, NullFormatter #, AutoMinorLocator
import numpy as np
import tkinter as tk
from tkinter import filedialog

# Get current directory where script is running
current_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()

# Find all CSV files in current directory
csv_files = [f for f in os.listdir(current_dir) if f.endswith('.csv')]

if not csv_files:
    print("Error: No CSV files found in current directory!")
    print(f"Current directory: {current_dir}")
    exit(1)

# Create a simple file selection dialog
root = tk.Tk()
root.withdraw()  # Hide the main window
root.title("Select CSV File")

# If only one CSV file, use it automatically
if len(csv_files) == 1:
    csv_filename = csv_files[0]
    print(f"Only one CSV file found, using: {csv_filename}")
else:
    # Show file selection dialog
    csv_filename = filedialog.askopenfilename(
        title="Select CSV file to plot",
        initialdir=current_dir,
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
    )
    
    if not csv_filename:
        print("No file selected. Exiting.")
        exit(1)
    
    # Get just the filename (not full path) for consistency
    csv_filename = os.path.basename(csv_filename)

root.destroy()  # Close the dialog window

# Read CSV file
print(f"Reading CSV file: {csv_filename}")

# Read comments from CSV header
comments = []
zeitpunkte = []
ratios = []
moving_averages = []
total_averages = []

# Read CSV and extract data
with open(csv_filename, 'r', encoding='utf-8') as f:
    reader = csv.reader(f)
    header_found = False
    
    for row in reader:
        # Read comment lines
        if row and row[0].startswith('#'):
            # Store comment (remove # and strip)
            comment = row[0][1:].strip()
            if comment:  # Only store non-empty comments
                comments.append(comment)
            continue
        
        # Find header row
        if 'Datum_Zeit' in str(row):
            header_found = True
            continue
        
        # Read data rows
        if header_found and len(row) >= 4:
            try:
                # Parse datetime
                dt = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
                ratio = float(row[1])
                moving_avg = float(row[2])
                total_avg = float(row[3])
                
                zeitpunkte.append(dt)
                ratios.append(ratio)
                moving_averages.append(moving_avg)
                total_averages.append(total_avg)
            except (ValueError, IndexError) as e:
                print(f"Warning: Skipping invalid row: {row} - {e}")
                continue

print(f"Loaded {len(ratios)} data points")
print(f"Found {len(comments)} comment lines")

if len(ratios) == 0:
    print("Error: No data found in CSV file!")
    exit(1)

# Calculate start datetime (first measurement)
start_datetime = zeitpunkte[0]
# Calculate end datetime (last measurement)
end_datetime = zeitpunkte[-1]

# Try to extract stop time from comments
stop_datetime = None
for comment in comments:
    if 'Stop:' in comment:
        try:
            # Extract datetime from comment like "Stop: 2025-12-01 08:00:00"
            stop_str = comment.split('Stop:')[1].strip()
            stop_datetime = datetime.strptime(stop_str, '%Y-%m-%d %H:%M:%S')
            break
        except:
            pass

# If not found in comments, use last measurement time
if stop_datetime is None:
    stop_datetime = end_datetime

# Calculate TOTAL time span for uniform tick spacing across all subplots
total_time_span_hours = (stop_datetime - start_datetime).total_seconds() / 3600.0
print(f"Total time span: {total_time_span_hours:.2f} hours")

# Determine UNIFORM tick spacing based on TOTAL time span (applies to all subplots)
# Use 6-hour intervals like in the 3rd subplot for consistency
if total_time_span_hours < 1.0:  # Less than 1 hour: use 10-minute intervals
    uniform_major_tick_spacing = 10.0 / 60.0  # 10 minutes in hours
    uniform_minor_tick_spacing = 1.0 / 60.0  # 1 minute minor ticks
elif total_time_span_hours < 6.0:  # Less than 6 hours: use 1-hour intervals
    uniform_major_tick_spacing = 1.0  # 1 hour
    uniform_minor_tick_spacing = 1.0 / 6  # 10 minutes minor ticks
else:  # 6 hours or more: use 6-hour intervals (like 3rd subplot)
    uniform_major_tick_spacing = 6.0  # 6 hours
    uniform_minor_tick_spacing = 1.0  # 1 hour minor ticks

print(f"Uniform tick spacing: Major={uniform_major_tick_spacing}h, Minor={uniform_minor_tick_spacing}h")

# Calculate ppm deviations from total average
# Use the last total average (should be consistent, but recalculate to be sure)
total_average_ratio = sum(ratios) / len(ratios)
ppm_deviations = [((ratio - total_average_ratio) / total_average_ratio) * 1e6 for ratio in ratios]

# Convert timepoints to minutes since start
minuten_seit_start = [(dt - start_datetime).total_seconds() / 60.0 for dt in zeitpunkte]

# Determine number of data points
total_points = len(ratios)
print(f"Total data points: {total_points}")

# Check if we need subplots (more than 10 minutes = 600 seconds = 600 points)
need_subplots = total_points > 600

if need_subplots:
    # Always use 3 subplots per page (landscape format)
    subplots_per_page = 3
    # Divide TIME into 3 equal parts, not data points!
    # This ensures correct time scaling even if measurement intervals are not constant
    total_time_span = (stop_datetime - start_datetime).total_seconds()
    time_per_subplot = total_time_span / subplots_per_page
    print(f"Creating {subplots_per_page} subplots per page")
    print(f"Total time span: {total_time_span/3600:.2f} hours")
    print(f"Time per subplot: {time_per_subplot/3600:.2f} hours")
else:
    subplots_per_page = 1
    time_per_subplot = (stop_datetime - start_datetime).total_seconds()
    print(f"Single plot (less than 10 minutes of data)")

# Create figure with exactly 3 subplots (A3 landscape format: 16.54 x 11.69 inches)
# A3 Landscape: 420x297mm = 16.54 x 11.69 inches
fig, axes = plt.subplots(subplots_per_page, 1, figsize=(16.54, 11.69), dpi=100, 
                         sharex=False, sharey=False)  # Each plot has own scaling

# Make axes a list for consistent handling
if subplots_per_page == 1:
    axes = [axes]

# Combine all comments into one line with "--" separator
if comments:
    main_title = " -- ".join(comments)
else:
    main_title = "Ratio Measurement"

# Create exactly 3 subplots
for plot_idx in range(subplots_per_page):
    ax = axes[plot_idx]
    
    # Calculate TIME range for this subplot (divide total TIME into 3 equal parts)
    # This ensures correct time scaling even if measurement intervals are not constant
    subplot_start_time = start_datetime + timedelta(seconds=plot_idx * time_per_subplot)
    subplot_end_time = start_datetime + timedelta(seconds=(plot_idx + 1) * time_per_subplot)
    
    # For the last subplot, extend to stop_datetime to include all data
    if plot_idx == subplots_per_page - 1:
        subplot_end_time = stop_datetime
    
    # Find data points that fall within this time range
    start_idx = None
    end_idx = None
    for i, dt in enumerate(zeitpunkte):
        if start_idx is None and dt >= subplot_start_time:
            start_idx = i
        if dt <= subplot_end_time:
            end_idx = i + 1
        elif dt > subplot_end_time:
            break
    
    # If no data found for this subplot, hide it
    if start_idx is None or end_idx is None or start_idx >= len(ppm_deviations):
        ax.axis('off')
        continue
    
    # Ensure indices are within bounds
    start_idx = max(0, start_idx)
    end_idx = min(end_idx, len(ppm_deviations))
    
    if start_idx >= end_idx:
        # Hide empty subplots
        ax.axis('off')
        continue
    
    print(f"Subplot {plot_idx + 1}: {end_idx - start_idx} points from {subplot_start_time.strftime('%Y-%m-%d %H:%M:%S')} to {subplot_end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Extract data for this subplot
    subplot_minuten = minuten_seit_start[start_idx:end_idx]
    subplot_ppm = ppm_deviations[start_idx:end_idx]
    
    # UNIFORM SCALING: Always use hours since start_datetime for all subplots
    subplot_x_data = [(dt - start_datetime).total_seconds() / 3600.0 for dt in zeitpunkte[start_idx:end_idx]]
    
    # Plot ppm deviations
    ax.plot(subplot_x_data, subplot_ppm, marker='o', 
            color='blue', markersize=1.5, linewidth=1.0, label='Ratio Deviation')
    
    # Red dashed line: Total average (deviation = 0)
    ax.axhline(y=0, color='red', linestyle='--', linewidth=1.5, alpha=0.7, label='Total Average')
    
    # Red solid line: Moving average
    window_size = 15
    if len(subplot_ppm) > 1:
        moving_avg_ppm = []
        moving_avg_times = []
        
        # Calculate moving average for this subplot's data range
        for i in range(start_idx, end_idx):
            if i < window_size - 1:
                # Expanding window for first 14 points
                window_data = ppm_deviations[0:i+1]
            else:
                # Fixed window from point 15
                window_start = i - window_size + 1
                window_data = ppm_deviations[window_start:i+1]
            
            moving_avg_ppm.append(sum(window_data) / len(window_data))
            # Use same X-axis scaling: hours since start_datetime
            moving_avg_times.append((zeitpunkte[i] - start_datetime).total_seconds() / 3600.0)
        
        if len(moving_avg_ppm) > 0:
            label_text = f'Moving Avg ({window_size} pts)'
            ax.plot(moving_avg_times, moving_avg_ppm, 
                   color='red', linestyle='-', linewidth=2, alpha=0.8, 
                   label=label_text)
    
    # Title with comments
    if plot_idx == 0:
        # First subplot: show main title (all comments in one line)
        title = main_title
    else:
        # Other subplots: no title (will be in X-axis label)
        title = ''
    
    ax.set_title(title, fontsize=10)
    
    # UNIFORM X-axis label: Always show hours since start_datetime
    # Include part number in X-axis label for all subplots
    if subplots_per_page > 1:
        xlabel = f'Part {plot_idx + 1} of {subplots_per_page} -- Zeit (Stunden seit {start_datetime.strftime("%Y-%m-%d %H:%M:%S")})'
    else:
        xlabel = f'Zeit (Stunden seit {start_datetime.strftime("%Y-%m-%d %H:%M:%S")})'
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel('Abweichung (ppm)', fontsize=10)
    
    ax.grid(True, alpha=0.3)
    if plot_idx == 0:  # Only show legend on first plot
        # Place legend at top center to avoid overlap with Overall Statistics
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.0), ncol=3, fontsize=8)
    
    # Y-axis formatting
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:.2f}'))
    
    # X-axis: Show data range for this subplot (individual scaling)
    if len(subplot_x_data) > 0:
        x_min = min(subplot_x_data)
        x_max = max(subplot_x_data)
        
        # Avoid identical limits
        if x_min == x_max:
            x_min = max(0, x_min - 0.1)
            x_max = x_max + 0.1
        else:
            # Add small margin
            margin = (x_max - x_min) * 0.02
            x_min = max(0, x_min - margin)
            x_max = x_max + margin
        
        ax.set_xlim(x_min, x_max)
        
        # UNIFORM TICK FORMATTING: All subplots use the same format
        # Align ticks to full hours and show actual time
        time_span = x_max - x_min
        
        # Custom locator class to align ticks to full hours
        class AlignedLocator(Locator):
            def __init__(self, base, offset=0):
                self.base = base
                self.offset = offset
            def __call__(self):
                vmin, vmax = self.axis.get_view_interval()
                # Calculate first tick position aligned to base
                first_tick = np.ceil((vmin - self.offset) / self.base) * self.base + self.offset
                ticks = np.arange(first_tick, vmax + self.base, self.base)
                return ticks[ticks >= vmin]
        
        # Calculate offset to align to next full hour from start_datetime
        minutes_to_next_hour = 60 - start_datetime.minute - start_datetime.second / 60.0
        offset_hours = minutes_to_next_hour / 60.0  # Offset in hours to reach next full hour
        
        # Calculate end time in hours since start for this subplot
        end_time_hours = (end_datetime - start_datetime).total_seconds() / 3600.0
        stop_time_hours = (stop_datetime - start_datetime).total_seconds() / 3600.0
        
        # Use UNIFORM tick spacing for ALL subplots (based on total time span, calculated once above)
        major_tick_spacing = uniform_major_tick_spacing
        minor_tick_spacing = uniform_minor_tick_spacing
        
        # Set minor ticks with uniform spacing
        ax.xaxis.set_minor_locator(MultipleLocator(minor_tick_spacing))
        
        # Custom locator that includes the stop time as the last tick
        class AlignedLocatorWithStop(Locator):
            def __init__(self, base, offset, stop_time):
                self.base = base
                self.offset = offset
                self.stop_time = stop_time
            def __call__(self):
                vmin, vmax = self.axis.get_view_interval()
                # Calculate first tick position aligned to base
                first_tick = np.ceil((vmin - self.offset) / self.base) * self.base + self.offset
                ticks = list(np.arange(first_tick, min(vmax, self.stop_time) + self.base, self.base))
                # Add stop time if it's within the view range
                if self.stop_time >= vmin and self.stop_time <= vmax:
                    if self.stop_time not in ticks:
                        ticks.append(self.stop_time)
                # Filter ticks within view range
                ticks = [t for t in ticks if t >= vmin and t <= vmax]
                return sorted(ticks)
        
        ax.xaxis.set_major_locator(AlignedLocatorWithStop(major_tick_spacing, offset_hours, stop_time_hours))
        
        # Format as actual time (hours since start_datetime)
        # Only format major ticks (full hours), minor ticks should have no labels
        # Show date change at midnight (IMPORTANT: date change indicator)
        # Store previous date to detect date changes
        previous_tick_date = [None]  # Use list to allow modification in nested function
        
        def hour_formatter(x, p):
            # x is hours since start_datetime, so add it to get actual time
            actual_time = start_datetime + timedelta(hours=x)
            # Round to nearest hour to handle floating point issues
            # Check if it's on the hour (minute should be 0 or very close)
            if actual_time.minute == 0 or (actual_time.minute < 1 and actual_time.second < 30):
                current_date = actual_time.date()
                
                # Check if date changed from previous tick (IMPORTANT: date change indicator)
                if previous_tick_date[0] is not None and current_date != previous_tick_date[0]:
                    # Date changed! Show date
                    previous_tick_date[0] = current_date
                    return actual_time.strftime('%Y-%m-%d\n%H:00')
                
                # Check if it's midnight (hour == 0) - always show date
                if actual_time.hour == 0:
                    previous_tick_date[0] = current_date
                    return actual_time.strftime('%Y-%m-%d\n00:00')
                else:
                    previous_tick_date[0] = current_date
                    return actual_time.strftime('%H:00')
            else:
                # This shouldn't happen for major ticks, but return empty string for safety
                return ''
        
        ax.xaxis.set_major_formatter(FuncFormatter(hour_formatter))
        # Explicitly remove minor tick labels
        ax.xaxis.set_minor_formatter(NullFormatter())
        
        # Custom coordinate formatter for mouse hover: show actual time instead of hours since start
        def format_coord(x, y):
            # x is in hours since start_datetime, convert to actual time
            if x is not None and not np.isnan(x):
                actual_time = start_datetime + timedelta(hours=x)
                time_str = actual_time.strftime('%Y-%m-%d %H:%M:%S')
                return f'x={time_str}, y={y:.2f} ppm'
            else:
                return f'x={x:.2f}, y={y:.2f} ppm'
        
        ax.format_coord = format_coord
    
    # Y-axis: Individual scaling for each subplot
    if len(subplot_ppm) > 0:
        y_min = min(subplot_ppm)
        y_max = max(subplot_ppm)
        # Add margin for better visibility
        y_range = y_max - y_min
        if y_range == 0:
            y_min = y_min - 1
            y_max = y_max + 1
        else:
            margin = y_range * 0.1  # 10% margin
            y_min = y_min - margin
            y_max = y_max + margin
        ax.set_ylim(y_min, y_max)
    
    # Statistics for this subplot
    if len(subplot_ppm) > 0:
        mean_dev = sum(subplot_ppm) / len(subplot_ppm)
        max_dev = max(subplot_ppm)
        min_dev = min(subplot_ppm)
        std_dev = (sum((x - mean_dev)**2 for x in subplot_ppm) / len(subplot_ppm))**0.5
        
        stats_text = (f'Mean: {mean_dev:.3f} ppm\n'
                    f'Max: {max_dev:.3f} ppm\n'
                    f'Min: {min_dev:.3f} ppm\n'
                    f'Std: {std_dev:.3f} ppm\n'
                    f'Points: {len(subplot_ppm)}')
        ax.text(0.02, 0.98, stats_text, transform=ax.transAxes,
               verticalalignment='top', bbox=dict(boxstyle='round', 
               facecolor='wheat', alpha=0.8), fontsize=8)

# Overall statistics (only on first subplot)
if len(ppm_deviations) > 0:
    mean_dev = sum(ppm_deviations) / len(ppm_deviations)
    max_dev = max(ppm_deviations)
    min_dev = min(ppm_deviations)
    std_dev = (sum((x - mean_dev)**2 for x in ppm_deviations) / len(ppm_deviations))**0.5
    
    overall_stats = (f'Overall Statistics:\n'
                    f'Mean: {mean_dev:.3f} ppm\n'
                    f'Max: {max_dev:.3f} ppm\n'
                    f'Min: {min_dev:.3f} ppm\n'
                    f'Std: {std_dev:.3f} ppm\n'
                    f'Total Points: {len(ppm_deviations)}')
    axes[0].text(0.98, 0.98, overall_stats, transform=axes[0].transAxes,
               verticalalignment='top', horizontalalignment='right',
               bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.8), fontsize=8)

plt.tight_layout()
print("Displaying plot...")
plt.show()
