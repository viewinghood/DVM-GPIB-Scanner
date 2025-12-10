# -*- coding: utf-8 -*-
"""
Created on Wed Nov 26 15:33:01 2025

@author: Heming

file:
    StahlRationScannerPyQtGraph.py
    
Ratio measurement plotter for Keithley 2100 (PyQtGraph version for better performance)
Main channel: 100V (fixed)
Ratio input pair: Auto (auto-ranging)
Deviation displayed in ppm (parts per million)

usage:
python StahlRationScannerPyQtGraph.py # default: until tomorrow 8 AM
python StahlRationScannerPyQtGraph.py 10m # measure for 10 minutes
python StahlRationScannerPyQtGraph.py 2h # measure for 2 hours
python StahlRationScannerPyQtGraph.py "2025-12-01 09:10:00" # measure until specific date/time
python StahlRationScannerPyQtGraph.py -h # show this help message or --help
"""

import pyvisa
import time
import numpy as np
from collections import deque
from datetime import datetime, timedelta
import csv
import os
import argparse
import re
import sys

# PyQtGraph imports
try:
    from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QLabel
    from PyQt5.QtCore import QTimer, Qt
    from PyQt5.QtGui import QFont
    import pyqtgraph as pg
    PYQT_VERSION = 5
except ImportError:
    try:
        # from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QLabel
        # from PyQt6.QtCore import QTimer, Qt
        # from PyQt6.QtGui import QFont
        import pyqtgraph as pg
        PYQT_VERSION = 6
    except ImportError:
        print("Error: PyQt5 or PyQt6 and pyqtgraph are required!")
        print("Install with: pip install pyqtgraph PyQt5")
        print("Or install with: pip install pyqtgraph PyQt6")
        sys.exit(1)

# Configure pyqtgraph for better rendering quality
pg.setConfigOptions(antialias=True)

# Kommandozeilenargumente parsen
def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Ratio measurement plotter for Keithley 2100 (PyQtGraph version)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s 10m              # Measure for 10 minutes
  %(prog)s 2h                # Measure for 2 hours
  %(prog)s 1d                # Measure for 1 day
  %(prog)s "2025-12-01 09:10:00"  # Measure until specific date/time
  %(prog)s -h                # Show this help message
        '''
    )
    
    parser.add_argument('duration', nargs='?', type=str,
                       help='Measurement duration (e.g., "10m", "2h", "1d") or end time (e.g., "2025-12-01 09:10:00")')
    
    args = parser.parse_args()
    return args

def parse_duration(duration_str, start_datetime):
    """Parse duration string or datetime string and return stop datetime"""
    if not duration_str:
        # Default: until tomorrow 8 AM
        tomorrow = start_datetime + timedelta(days=1)
        return tomorrow.replace(hour=8, minute=0, second=0, microsecond=0)
    
    # Try to parse as duration (e.g., "10m", "2h", "1d")
    duration_match = re.match(r'^(\d+)([mhd])$', duration_str.lower())
    if duration_match:
        value = int(duration_match.group(1))
        unit = duration_match.group(2)
        
        if unit == 'm':
            return start_datetime + timedelta(minutes=value)
        elif unit == 'h':
            return start_datetime + timedelta(hours=value)
        elif unit == 'd':
            return start_datetime + timedelta(days=value)
    
    # Try to parse as datetime string
    datetime_formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
        '%d.%m.%Y %H:%M:%S',
        '%d.%m.%Y %H:%M',
        '%d.%m.%Y'
    ]
    
    for fmt in datetime_formats:
        try:
            parsed_dt = datetime.strptime(duration_str, fmt)
            # If no time specified, default to 8:00 AM
            if fmt in ['%Y-%m-%d', '%d.%m.%Y']:
                parsed_dt = parsed_dt.replace(hour=8, minute=0, second=0, microsecond=0)
            return parsed_dt
        except ValueError:
            continue
    
    # If parsing fails, raise error
    raise ValueError(f"Could not parse duration/datetime: '{duration_str}'. "
                    f"Use format like '10m', '2h', '1d' or '2025-12-01 09:10:00'")

# Parse command line arguments
args = parse_arguments()

# Kommunikation
rm = pyvisa.ResourceManager()
DMM = None
name_res = None
for addr in rm.list_resources():
    try:
        DMM = rm.open_resource(addr)
        name_res = DMM.query('*IDN?').strip()
        # Only print if we got a valid IDN response (not just the command echoed back)
        if name_res and name_res != '*IDN?':
            print(f"{addr}: {name_res}")
            break  # get first valid instrument
        DMM.close()
        DMM = None
    except Exception:
        # Silently continue on error - only print connected instruments
        continue

if DMM is None:
    raise RuntimeError("No valid instrument found!")

# Setze Timeout für Messungen (länger für 1 Sekunde Integration)
DMM.timeout = 5000  # 5 Sekunden Timeout (sollte ausreichen für 1 Sekunde Integration)

# Configure DMM for ratio measurement
print("Configuring DMM for ratio measurement...")

# Clear any previous errors and status
try:
    DMM.write("*CLS")  # Clear status register
    time.sleep(0.2)
except:
    pass

print("Checking available functions...")
try:
    # Query current function to see what's available
    current_func = DMM.query(":SENS:FUNC?")
    print(f"Current function: {current_func.strip()}")
except Exception as e:
    print(f"Could not query function: {e}")

try:
    # Set to ratio measurement mode (Keithley 2100 requires double quotes)
    # Try different possible command formats
    ratio_configured = False
    
    # Try VOLT:DC:RAT format
    try:
        DMM.write(':SENS:FUNC "VOLT:DC:RAT"')
        time.sleep(0.1)  # Small delay for instrument to process
        response = DMM.query(":SENS:FUNC?")
        if "RAT" in response.upper():
            ratio_configured = True
            print("Ratio mode set using VOLT:DC:RAT")
    except Exception as e1:
        print(f"Failed with VOLT:DC:RAT: {e1}")
    
    # Try VOLT:DC:RATIO format if first failed
    if not ratio_configured:
        try:
            DMM.write(':SENS:FUNC "VOLT:DC:RATIO"')
            time.sleep(0.1)
            response = DMM.query(":SENS:FUNC?")
            if "RAT" in response.upper():
                ratio_configured = True
                print("Ratio mode set using VOLT:DC:RATIO")
        except Exception as e2:
            print(f"Failed with VOLT:DC:RATIO: {e2}")
    
    if not ratio_configured:
        raise Exception("Could not set ratio measurement mode")
    
    time.sleep(0.3)  # Delay after setting function
    
    # Main channel range: 100V
    # Check current range first, only set if different
    try:
        current_range = DMM.query(":SENS:VOLT:DC:RANG?")
        current_val = float(current_range.strip().replace('V', '').replace('+', '').replace('E+0', 'E'))
        print(f"Current main channel range: {current_range.strip()}")
        
        # Only set if not already 100V (within tolerance)
        if abs(current_val - 100.0) > 1.0:
            try:
                DMM.write(":SENS:VOLT:DC:RANG 100")
                time.sleep(0.3)
                main_range = DMM.query(":SENS:VOLT:DC:RANG?")
                print(f"Main channel range set to: {main_range.strip()}")
            except Exception as e_set:
                print(f"Could not change range (may already be correct): {e_set}")
        else:
            print("Main channel range already at 100V")
    except Exception as e_query:
        print(f"Could not query range, trying to set: {e_query}")
        try:
            DMM.write(":SENS:VOLT:DC:RANG 100")
            time.sleep(0.3)
            print("Main channel range set to 100V (assumed)")
        except:
            print("Warning: Could not set main range. Continuing...")
    
    time.sleep(0.3)  # Delay before setting reference range
    
    # Reference channel: Enable auto-ranging
    # For ratio measurements, reference channel auto-range may be automatic
    # Try to enable it, but don't fail if it doesn't work
    try:
        # Try to enable auto-range (this may not be needed for ratio mode)
        DMM.write(":SENS:VOLT:DC:RANG:AUTO ON")
        time.sleep(0.2)
        print("Reference channel auto-range enabled")
    except Exception as e_auto:
        # Auto-range may already be on or not applicable for ratio mode
        print(f"Note: Reference channel will use auto-ranging (ratio mode default)")
    
    time.sleep(0.2)  # Delay before setting NPLC
    
    # Set integration time to 1 second
    # NPLC (Number of Power Line Cycles): 1 second = 50 NPLC (50Hz) or 60 NPLC (60Hz)
    # Using 50 NPLC for 1 second integration time (assuming 50Hz power line)
    try:
        DMM.write(":SENS:VOLT:DC:NPLC 50")  # 50 NPLC ≈ 1 second at 50Hz
        time.sleep(0.1)
        nplc = DMM.query(":SENS:VOLT:DC:NPLC?")
        print(f"Integration time (NPLC): {nplc.strip()} (~1 second)")
    except:
        print(f"Could not set NPLC...")
    
    print("DMM configured: Main=100V, Reference=Auto")
except Exception as e:
    print(f"Error configuring DMM settings: {e}")
    print("Trying to continue with current settings...")
    # Try to query what's currently set
    try:
        func = DMM.query(":SENS:FUNC?")
        print(f"Current measurement function: {func.strip()}")
    except:
        pass

# Listen für Zeit und Messwerte - für Tage mit 1 Messung pro Sekunde
# 7 Tage = 7 * 24 * 60 * 60 = 604800 Sekunden, mit Puffer für mehrere Tage
MAX_DATA_POINTS = 700000  # ~8 Tage bei 1 Messung/Sekunde
zeiten = deque(maxlen=MAX_DATA_POINTS)  # Zeit in Sekunden seit Start
zeitpunkte = deque(maxlen=MAX_DATA_POINTS)  # Datetime-Objekte für Anzeige
ratios = deque(maxlen=MAX_DATA_POINTS)  # Ratio-Werte
ppm_deviations = deque(maxlen=MAX_DATA_POINTS)  # Abweichung in ppm

start_time = time.time()
start_datetime = datetime.now()
reference_ratio = None  # Wird nach erstem Messwert gesetzt
measurement_stopped = False  # Flag to stop measurements

# Optimized running statistics (avoid recalculating mean every time)
total_ratio_sum = 0.0  # Running sum of all ratios
total_average_ratio = 0.0  # Current mean (updated incrementally)

# Calculate stop time from command line argument or use default
try:
    stop_datetime = parse_duration(args.duration, start_datetime)
except ValueError as e:
    print(f"Error: {e}")
    print("Use -h for help")
    exit(1)

stop_time = time.time() + (stop_datetime - start_datetime).total_seconds()

# Prüfe ob Stopp-Zeit in der Zukunft liegt
if stop_time <= time.time():
    print(f"WARNING: Stop time {stop_datetime.strftime('%Y-%m-%d %H:%M:%S')} is in the past!")
    print("Please specify a future time.")
    exit(1)

duration_str = str(stop_datetime - start_datetime).split('.')[0]  # Remove microseconds
print(f"Measurement duration: {duration_str}")
print(f"Measurement will stop at: {stop_datetime.strftime('%Y-%m-%d %H:%M:%S')}")

# CSV-Datei erstellen
# Get current directory where script is running
current_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
csv_filename = f"Driftmessung_{start_datetime.strftime('%Y-%m-%d_%H-%M-%S')}.csv"
csv_full_path = os.path.join(current_dir, csv_filename)
csv_file = open(csv_filename, 'w', newline='', encoding='utf-8')
csv_writer = csv.writer(csv_file, delimiter=',')

# Header mit Kommentar schreiben (ohne Anführungszeichen - direkt als Text schreiben)
csv_file.write(f"# Ratio Measurement - {name_res}\n")
csv_file.write(f"# Main Channel: 100V, Reference: Auto\n")
csv_file.write(f"# Start: {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}\n")
csv_file.write(f"# Stop: {stop_datetime.strftime('%Y-%m-%d %H:%M:%S')}\n")
csv_file.write(f"# File: {csv_full_path}\n")
csv_file.write("\n")  # Leere Zeile
csv_writer.writerow(['Datum_Zeit', 'Ratiomesswert', 'Gleitender_Mittelwert', 'Gesamtmittelwert'])
csv_file.flush()  # Sofort schreiben
print(f"CSV file created: {csv_full_path}")

# Messwert einlesen und in float umwandeln
def generiere_ratio():
    try:
        # Method 1: Use INIT + FETCH? (recommended for single measurements)
        # INIT starts measurement, FETCH? waits for completion and retrieves result
        # Note: FETCH? already waits, so *OPC is not strictly necessary
        DMM.write("INIT")  # Initiate measurement
        wert = DMM.query("FETCH?")  # Wait for measurement to complete and fetch result
        ratio = float(wert)
        print(f"Ratio: {ratio:.10f}", end='\r')  # Kein Newline, überschreibt Zeile
        return ratio
    except Exception as e:
        # Try alternative method if FETCH? fails
        try:
            # Method 2: Use READ? (does INIT + FETCH in one command)
            wert = DMM.query("READ?")  # READ? = INIT + FETCH? in one command
            ratio = float(wert)
            print(f"Ratio: {ratio:.10f}", end='\r')
            return ratio
        except Exception as e2:
            # Check for errors in instrument and display them
            error_msg = None
            try:
                error = DMM.query("SYST:ERR?")
                if error and "0," not in error and "0,\"" not in error:
                    error_msg = error.strip()
            except:
                pass
            
            # Display error (don't clear it - user wants to see errors)
            if error_msg:
                print(f"\n[ERROR] Instrument: {error_msg}")
                print(f"[ERROR] Exception: {e2}")
            else:
                print(f"\n[ERROR] Exception: {e2}")
            return None

# PyQtGraph Main Window Class
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ratio Measurement - Keithley 2100")
        self.setGeometry(100, 100, 1700, 500)  # Same size as matplotlib version
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout()
        central_widget.setLayout(layout)
        
        # Title label
        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setFont(QFont("Arial", 10, QFont.Bold))
        layout.addWidget(self.title_label)
        
        # Plot widget - matplotlib-like styling
        self.plot_widget = pg.PlotWidget()
        
        # White background (matplotlib style)
        self.plot_widget.setBackground('w')
        
        # Black axes and labels (matplotlib style)
        self.plot_widget.getAxis('left').setPen(pg.mkPen(color='k', width=1))
        self.plot_widget.getAxis('bottom').setPen(pg.mkPen(color='k', width=1))
        self.plot_widget.getAxis('left').setTextPen(pg.mkPen(color='k'))
        self.plot_widget.getAxis('bottom').setTextPen(pg.mkPen(color='k'))
        
        # Labels
        self.plot_widget.setLabel('left', 'Abweichung (ppm)', color='k')
        self.plot_widget.setLabel('bottom', 'Zeit (Minuten)', color='k')
        
        # Grid with matplotlib-like gray color
        # Reduce grid density by showing grid only for major ticks (factor 2 reduction)
        # In pyqtgraph, we can control this via showGrid parameters
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        # Set grid color to light gray (matplotlib default)
        self.plot_widget.getAxis('left').setGrid(255 * 0.3)  # alpha=0.3 equivalent
        self.plot_widget.getAxis('bottom').setGrid(255 * 0.3)
        # Reduce subdivisions: pyqtgraph uses auto-subdivisions, we can't directly control
        # but we can reduce grid density by adjusting the auto-tick spacing
        # The grid will automatically be less dense if we have fewer auto-generated ticks
        
        # Set style with tickTextOffset
        left_axis = self.plot_widget.getAxis('left')
        bottom_axis = self.plot_widget.getAxis('bottom')
        left_axis.setStyle(tickTextOffset=5)
        bottom_axis.setStyle(tickTextOffset=5)
        # Note: Subdivisions reduction will be handled in update_plot
        # by setting custom tick spacing that results in fewer minor ticks
        
        # Legend with matplotlib-like styling (inline, centered at top)
        # Position legend at top center, similar to matplotlib's 'upper center'
        # offset is (x, y) from top-left corner of plot area
        # We'll position it dynamically in update_plot based on plot width
        self.legend = self.plot_widget.addLegend(offset=(0, 10), labelTextSize='9pt')
        # Store reference to legend for later positioning
        
        # Set axis tick color to black
        self.plot_widget.getAxis('left').setTickPen(pg.mkPen(color='k'))
        self.plot_widget.getAxis('bottom').setTickPen(pg.mkPen(color='k'))
        
        layout.addWidget(self.plot_widget)
        
        # Statistics label (matplotlib-like wheat background)
        # We'll position it as an overlay on the plot widget
        # Position it in bottom-left to avoid overlapping with legend (which is top-left)
        self.stats_label = QLabel(self.plot_widget)
        self.stats_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        # Matplotlib default bbox color: wheat with alpha 0.8
        # RGB: (245, 222, 179) = wheat color
        self.stats_label.setStyleSheet("background-color: rgba(245, 222, 179, 204); padding: 8px; border: 1px solid rgba(0, 0, 0, 0.3); border-radius: 5px;")
        self.stats_label.setFont(QFont("Courier", 9))
        # Position stats label in bottom-left corner of plot (overlay) - will be set in update_plot
        self.stats_label.raise_()  # Bring to front
        
        # Plot items (will be updated, not recreated)
        self.plot_data = None
        self.plot_zero_line = None
        self.plot_moving_avg = None
        
        # Timer for measurements (fast, doesn't block)
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_measurement)
        self.timer.start(100)  # 100ms interval (same as matplotlib version)
        
        # Separate timer for plot updates (slower, doesn't interfere with measurements)
        self.plot_timer = QTimer()
        self.plot_timer.timeout.connect(self.update_plot)
        self.plot_timer.start(1000)  # Update plot every 1 second
        
    def update_plot(self):
        """Update the plot with current data"""
        if len(zeiten) == 0:
            return
        
        # Konvertiere Zeitpunkte für X-Achse (Minuten seit Start)
        minuten_seit_start = np.array([(dt - start_datetime).total_seconds() / 60.0 
                                       for dt in zeitpunkte])
        
        # Use pre-calculated total average (optimized, no recalculation needed)
        # Berechne Abweichungen vom Gesamtmittelwert (falls noch nicht berechnet)
        if len(ppm_deviations) < len(ratios):
            # Recalculate deviations if needed (shouldn't happen often)
            global total_average_ratio
            ppm_deviations.clear()
            for ratio in ratios:
                deviation_ppm = ((ratio - total_average_ratio) / total_average_ratio) * 1e6
                ppm_deviations.append(deviation_ppm)
        
        # Convert to numpy arrays for performance
        x_data = minuten_seit_start
        y_data = np.array(list(ppm_deviations))
        
        # Clear previous plots (this also clears the legend)
        self.plot_widget.clear()
        
        # Plot der ppm-Abweichung (matplotlib blue color)
        # Matplotlib default blue: #1f77b4 - use hex string for pyqtgraph
        blue_color = '#1f77b4'  # matplotlib blue
        self.plot_data = self.plot_widget.plot(x_data, y_data, 
                                              pen=pg.mkPen(color=blue_color, width=2.5),
                                              symbol='o', symbolSize=3,
                                              symbolPen=pg.mkPen(color=blue_color, width=1),
                                              symbolBrush=pg.mkBrush(color=blue_color),
                                              name='Ratio Deviation')
        
        # Grüne gestrichelte Linie: Gesamtmittelwert (Abweichung = 0)
        # Light green color: #00ff00
        green_color = '#00ff00'  # light green
        dash_pen = pg.mkPen(color=green_color, width=2.5, style=pg.QtCore.Qt.DashLine)
        self.plot_zero_line = self.plot_widget.plot([x_data[0], x_data[-1]], [0, 0],
                                                    pen=dash_pen,
                                                    name='Total Average')
        
        # Rote durchgezogene Linie: Gleitender Mittelwert
        # Expanding window für Punkte 1-14, dann fixed window (15 Punkte) ab Punkt 15
        if len(ppm_deviations) > 1:
            window_size = 15
            moving_avg = []
            moving_avg_times = []
            
            for i in range(len(ppm_deviations)):
                if i < window_size - 1:
                    # Expanding window für erste 14 Punkte (1, 2, 3, ... 14 Punkte)
                    window_data = list(ppm_deviations)[0:i+1]
                else:
                    # Fixed window ab Punkt 15 (immer 15 Punkte)
                    start_idx = i - window_size + 1
                    window_data = list(ppm_deviations)[start_idx:i+1]
                
                moving_avg.append(sum(window_data) / len(window_data))
                moving_avg_times.append(minuten_seit_start[i])
            
            if len(moving_avg) > 0:
                label_text = f'Moving Avg (expanding→{window_size} pts)' if len(ppm_deviations) < window_size else f'Moving Avg ({window_size} pts)'
                # Use same matplotlib red color
                red_color = '#d62728'  # matplotlib red
                self.plot_moving_avg = self.plot_widget.plot(moving_avg_times, moving_avg,
                                                            pen=pg.mkPen(color=red_color, width=3),
                                                            name=label_text)
        
        # Update title
        current_time = datetime.now()
        remaining_time_minutes = round((stop_datetime - current_time).total_seconds() / 60.0)
        if remaining_time_minutes < 0:
            remaining_time_minutes = 0
        title = f'Ratio Deviation @ {name_res}\nMain: 100V, Ref: Auto | Remaining time: {remaining_time_minutes} min'
        if measurement_stopped:
            title += '\n[Measurement finished successfully]'
        self.title_label.setText(title)
        
        # Create and center legend at top (matplotlib 'upper center' style)
        # Calculate center position - try multiple methods to get plot width
        plot_width = 0
        # Method 1: Try view box geometry
        view_box = self.plot_widget.getViewBox()
        if view_box is not None:
            view_rect = view_box.geometry()
            plot_width = view_rect.width()
        
        # Method 2: If view box not available, use widget width
        if plot_width <= 0:
            plot_width = self.plot_widget.width()
        
        # Calculate legend position
        if plot_width > 0:
            # Estimate legend width (approximately 250 pixels for 3 items)
            legend_width = 250
            legend_x_offset = max(10, (plot_width - legend_width) // 2)
        else:
            # Default to center if width not available (will be updated on next call)
            legend_x_offset = 350  # Approximate center for 1700px wide plot
        
        # Create legend with centered position
        self.legend = self.plot_widget.addLegend(offset=(legend_x_offset, 10), labelTextSize='9pt')
        
        # X-Achse: Zeige alle verfügbaren Daten
        if len(minuten_seit_start) > 0:
            x_min = float(min(minuten_seit_start))
            x_max = float(max(minuten_seit_start))
            # Avoid identical limits (happens with single data point)
            if x_min == x_max:
                # Add small range around the single point
                x_min = max(0, x_min - 0.1)
                x_max = x_max + 0.1
            self.plot_widget.setXRange(x_min, x_max, padding=0.02)
        
        # Statistik anzeigen
        if len(ppm_deviations) > 0:
            mean_dev = sum(ppm_deviations) / len(ppm_deviations)
            max_dev = max(ppm_deviations)
            min_dev = min(ppm_deviations)
            std_dev = (sum((x - mean_dev)**2 for x in ppm_deviations) / len(ppm_deviations))**0.5
            
            # Zähle fehlgeschlagene Messungen (None-Werte)
            failed_count = len(ratios) - len(ppm_deviations)
            
            stats_text = (f'Mean: {mean_dev:.3f} ppm\n'
                        f'Max: {max_dev:.3f} ppm\n'
                        f'Min: {min_dev:.3f} ppm\n'
                        f'Std: {std_dev:.3f} ppm\n'
                        f'Points: {len(ppm_deviations)}')
            if failed_count > 0:
                stats_text += f'\nFailed: {failed_count}'
            self.stats_label.setText(stats_text)
            
            # Position stats label in bottom-left corner (to avoid legend overlap)
            # Legend is at top-left (offset 10, 10), so we put stats at bottom-left
            plot_height = self.plot_widget.height()
            stats_height = 120  # Approximate height of stats box
            stats_width = 150   # Approximate width of stats box
            self.stats_label.setGeometry(10, plot_height - stats_height - 10, stats_width, stats_height)
    
    def update_measurement(self):
        """Update function called by timer"""
        global reference_ratio, measurement_stopped
        
        # Check if measurement should be stopped
        if measurement_stopped:
            return
        
        current_time = time.time()
        
        # Check if we've reached stop time
        if current_time >= stop_time:
            measurement_stopped = True
            print(f"\nStop time reached ({stop_datetime.strftime('%Y-%m-%d %H:%M:%S')}). Stopping measurement...")
            print("Final plot will remain open. Close the window when done viewing.")
            self.timer.stop()
            self.plot_timer.stop()
            # Do one final update to show all data
            if len(zeiten) > 0:
                self.update_plot()
                save_to_csv()  # Finale Speicherung
            return
        
        # Perform measurement immediately (synchronized with multimeter readiness)
        # The timestamp is set when FETCH actually returns (inside generiere_ratio)
        neuer_ratio = generiere_ratio()
        
        # Nur gültige Werte hinzufügen
        if neuer_ratio is not None:
            # Get timestamp when measurement was actually taken (after FETCH returns)
            aktuelles_datetime = datetime.now()
            aktuelle_zeit = time.time() - start_time
            
            zeiten.append(aktuelle_zeit)
            zeitpunkte.append(aktuelles_datetime)
            ratios.append(neuer_ratio)
            
            # Update running statistics (incremental, much faster than recalculating)
            global total_ratio_sum, total_average_ratio
            total_ratio_sum += neuer_ratio
            total_average_ratio = total_ratio_sum / len(ratios)
            
            # Berechne Abweichung vom Gesamtmittelwert aller Messungen
            deviation_ppm = ((neuer_ratio - total_average_ratio) / total_average_ratio) * 1e6
            ppm_deviations.append(deviation_ppm)
            
            # Write to CSV immediately after measurement (FETCH arrived and data is ready)
            save_to_csv()
            
            # Plot update is handled by separate timer (doesn't block measurements)
        else:
            # Measurement failed - plot will be updated by timer
            pass
    
    def closeEvent(self, event):
        """Handle window close event"""
        global measurement_stopped
        measurement_stopped = True
        self.timer.stop()
        self.plot_timer.stop()
        if DMM is not None:
            DMM.close()
        rm.close()
        # Finale CSV-Speicherung
        if len(ratios) > 0:
            save_to_csv()
        if csv_file is not None:
            csv_file.close()
            print(f"CSV file saved: {csv_filename}")
        print("\nMeasurement finished successfully. Resources closed.")
        event.accept()

# Funktion zum Speichern in CSV
def save_to_csv():
    """Speichere aktuelle Daten in CSV"""
    global csv_writer, csv_file
    
    if len(ratios) == 0:
        return
    
    # Use pre-calculated total average (optimized, no recalculation needed)
    global total_average_ratio
    
    # Berechne Gleitenden Mittelwert (ab Punkt 15)
    window_size = 15
    moving_averages = []
    
    for i in range(len(ratios)):
        if i < window_size - 1:
            # Expanding window für erste 14 Punkte
            window_data = list(ratios)[0:i+1]
            moving_avg = sum(window_data) / len(window_data)
        else:
            # Fixed window ab Punkt 15
            start_idx = i - window_size + 1
            window_data = list(ratios)[start_idx:i+1]
            moving_avg = sum(window_data) / len(window_data)
        moving_averages.append(moving_avg)
    
    # Zähle bereits geschriebene Zeilen (ohne Header)
    try:
        with open(csv_filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            # Zähle Datenzeilen (nicht Kommentare/Header)
            written_lines = sum(1 for line in lines if line.strip() and not line.strip().startswith('#') and 'Datum_Zeit' not in line)
    except:
        written_lines = 0
    
    # Schreibe nur neue Zeilen
    if len(ratios) > written_lines:
        for i in range(written_lines, len(ratios)):
            # ISO-Format für Datum/Zeit (gut für Excel/matplotlib)
            dt_str = zeitpunkte[i].strftime('%Y-%m-%d %H:%M:%S')
            csv_writer.writerow([dt_str, f'{ratios[i]:.10f}', 
                                f'{moving_averages[i]:.10f}', 
                                f'{total_average_ratio:.10f}'])
        csv_file.flush()  # Sofort schreiben

# Create application
app = QApplication(sys.argv)

# Create main window
window = MainWindow()

# Anzeige starten
print(f"\nStarting ratio measurement at {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
print("Measurement interval: 1 second")
print("Close the plot window to stop measurement.\n")

# Show window
window.show()

# Run application
try:
    sys.exit(app.exec())
finally:
    # Sicherstellen, dass Ressourcen geschlossen werden
    # Finale CSV-Speicherung
    if len(ratios) > 0:
        save_to_csv()
    if csv_file is not None:
        csv_file.close()
        print(f"CSV file saved: {csv_filename}")
    if DMM is not None:
        DMM.close()
    rm.close()
    print("\nMeasurement finished successfully. Resources closed.")
