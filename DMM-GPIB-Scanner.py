# -*- coding: utf-8 -*-
"""
Created on Wed Nov 26 15:33:01 2025

@author: Heming

file:
    DMM-GPIB-Scanner.py

GPIB Scanner for DMM devices with support for:
- Legacy instruments (via GPIB-USB adapter):
  * Iwatsu VOAC 7513 (DC volts measurement)
  * Prema 6000 (DC volts measurement)
  * Easily extensible to more legacy instruments
- Modern instruments (USB/GPIB):
  * Keithley 2100 (Ratio measurement)
  * Main channel: 100V (fixed)
  * Ratio input pair: Auto (auto-ranging)

Features:
- Automatic instrument detection (legacy vs modern)
- DC volts measurement for legacy instruments
- Ratio measurement for Keithley 2100 (unchanged)
- Deviation displayed in ppm (parts per million)
- Real-time plotting and CSV logging

usage:
python DMM-GPIB-Scanner.py # default: until tomorrow 8 AM (auto-detect instrument)
python DMM-GPIB-Scanner.py 10m # measure for 10 minutes (auto-detect instrument)
python DMM-GPIB-Scanner.py 2h # measure for 2 hours
python DMM-GPIB-Scanner.py "2025-12-01 09:10:00" # measure until specific date/time
python DMM-GPIB-Scanner.py legacy 10m # force legacy mode, measure for 10 minutes
python DMM-GPIB-Scanner.py legacy 2h # force legacy mode, measure for 2 hours
python DMM-GPIB-Scanner.py -h # show this help message or --help

Testing in IPython
    runfile('C:/WinPython/notebooks/HiPES/Francois/DMM-GPIB-Scanner.py', args='legacy 2m')
    
"""

import pyvisa
import time
import sys
import matplotlib
# Set backend for IPython/Spyder to ensure blocking behavior
# Check if we're in an interactive environment before importing pyplot
is_ipython = 'IPython' in sys.modules
is_spyder = 'spyder' in str(sys.modules).lower() or 'spyder_kernels' in str(sys.modules)

if is_ipython or is_spyder:
    # Force a GUI backend that supports blocking in interactive environments
    try:
        matplotlib.use('TkAgg')  # TkAgg usually works well for blocking
    except:
        try:
            matplotlib.use('Qt5Agg')  # Fallback to Qt5
        except:
            pass  # Use default if neither works

import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.ticker import EngFormatter
from collections import deque
from datetime import datetime, timedelta
import csv
import os
import argparse
import re

# ============================================================================
# Legacy Instrument Support - Modular System
# ============================================================================

class LegacyInstrument:
    """Base class for legacy DMM instruments"""
    def __init__(self, name, dmm_resource):
        self.name = name
        self.dmm = dmm_resource
        self.is_legacy = True
    
    def configure_dc_volts(self):
        """Configure instrument for DC volts measurement. Override in subclasses."""
        raise NotImplementedError("Subclass must implement configure_dc_volts")
    
    def parse_measurement(self, response):
        """Parse measurement response and return float value. Override in subclasses."""
        raise NotImplementedError("Subclass must implement parse_measurement")
    
    def trigger_measurement(self):
        """Trigger a measurement and return the response. Override in subclasses."""
        raise NotImplementedError("Subclass must implement trigger_measurement")
    
    def get_measurement_value(self):
        """Get a measurement value (trigger + parse). Override if needed."""
        response = self.trigger_measurement()
        return self.parse_measurement(response)


class IwatsuVOAC7513(LegacyInstrument):
    """Iwatsu VOAC 7513 legacy DMM
    
    Response format after "G" command: "DV  +4.09999E+0" + Delimiter
    - First 4 characters before the '+' of the floating point value:
      1. First 2 characters: Measurement function (e.g., "DV" for DC voltage)
      2. 3rd character: Can be "O" for overflow
      3. 4th character: Can be "R" for relative measurement
    - Examples: "DV  ", "DVO ", "DVR ", "DVOR"
    - Mantissa: up to 6 digits, first digit max "4", highest value "4.09999"
    
    Status Byte (STB) structure (bit 7 to bit 0, highest to lowest):
    - Bit 7: CAL (Calibration = 1)
    - Bit 6: SRQ (Service Request = 1, new data available)
    - Bit 5: ERR (Error = 1)
    - Bit 4: BUSY (measuring = 1)
    - Bit 3: PSRQ (Front panel SRQ = 1, when user presses SRQ on front panel)
    - Bit 2: STRE (Stored at end, DMM can store data and indicates that. Cleared by DC)
    - Bit 1: CPLT (Complete: measurement end, cleared by "G" Get command or DC)
    - Bit 0: (reserved/unused)
    
    Status Byte (STB) structure (bit 7 to bit 0, highest to lowest):
    - Bit 7: CAL (Calibration = 1)
    - Bit 6: SRQ (Service Request = 1, new data available)
    - Bit 5: ERR (Error = 1)
    - Bit 4: BUSY (measuring = 1)
    - Bit 3: PSRQ (Front panel SRQ = 1, when user presses SRQ on front panel)
    - Bit 2: STRE (Stored at end, DMM can store data and indicates that. Cleared by DC)
    - Bit 1: CPLT (Complete: measurement end, cleared by "G" Get command or DC)
    - Bit 0: (reserved/unused)
    
    NOTE: Iwatsu has different GPIB behavior than Prema 6000!
    - Device Clear, SRQ handling, S1/S0 commands, etc. may work differently
    - Will need to be tested and implemented separately when working with Iwatsu
    - Current implementation is basic and may need refinement for Iwatsu-specific behavior
    """
    
    def __init__(self, dmm_resource):
        super().__init__("Iwatsu VOAC 7513", dmm_resource)
    
    def decode_status_byte(self, stb):
        """Decode Iwatsu status byte and return status information
        
        Args:
            stb: Status byte value (integer)
            
        Returns:
            dict: Dictionary with status flags
        """
        if stb is None:
            return None
        
        return {
            'CAL': bool(stb & 0x80),      # Bit 7: Calibration
            'SRQ': bool(stb & 0x40),      # Bit 6: Service Request (new data available)
            'ERR': bool(stb & 0x20),      # Bit 5: Error
            'BUSY': bool(stb & 0x10),     # Bit 4: Measuring
            'PSRQ': bool(stb & 0x08),     # Bit 3: Front panel SRQ
            'STRE': bool(stb & 0x04),     # Bit 2: Stored at end (cleared by DC)
            'CPLT': bool(stb & 0x02),     # Bit 1: Complete (cleared by "G" or DC)
            'raw': stb,
            'hex': hex(stb)
        }
    
    def configure_dc_volts(self):
        """Configure Iwatsu for DC volts measurement.
        
        Command reference:
        - F0 = DC volts, F1 = AC Volts, F2 = 2-wire ohms, F3 = DC amps, F4 = AC amps, F5 = temperature (°C)
        - R0 = Autorange ON
        - S0 = slow (maximum resolution, default, after DC)
        - X0 = "Hold" (single measurement), X2 = continuously measurement
        - JA0 = Measurement data output
        - JH1 = value & Header, JH0 = only value
        - AE1 = SRQ if measurement END, AE0 = no SRQ (default)
        - "G" command triggers measurement if in X0 (Hold) mode
        
        Configuration: F0 (DC volts), R0 (Autorange), S0 (slow/max resolution), 
                       X0 (Hold/single), JA0 (measurement data), JH1 (value & header), AE1 (SRQ on end)
        """
        try:
            # Configure Iwatsu step by step
            # F0 = DC volts mode
            self.dmm.write("F0")
            time.sleep(0.1)
            
            # R0 = Autorange ON
            self.dmm.write("R0")
            time.sleep(0.1)
            
            # S0 = slow (maximum resolution, default after DC)
            self.dmm.write("S0")
            time.sleep(0.1)
            
            # X0 = "Hold" mode (single measurement - "G" command triggers one measurement)
            self.dmm.write("X0")
            time.sleep(0.1)
            
            # JA0 = Measurement data output
            self.dmm.write("JA0")
            time.sleep(0.1)
            
            # JH1 = value & Header (for full format with function code like "DV  +4.09999E+0")
            self.dmm.write("JH1")
            time.sleep(0.1)
            
            # AE1 = SRQ if measurement END (enable SRQ when measurement completes)
            self.dmm.write("AE1")
            time.sleep(0.2)
            
            print("Iwatsu configured for DC volts measurement:")
            print("  F0 (DC volts), R0 (Autorange), S0 (slow/max resolution)")
            print("  X0 (Hold/single), JA0 (measurement data), JH1 (value & header), AE1 (SRQ on end)")
            return True
        except Exception as e:
            print(f"Error configuring Iwatsu: {e}")
            return False
    
    def parse_measurement(self, response):
        """Parse Iwatsu response format: 'DV  +4.09999E+0' + Delimiter
        
        Format specification:
        - First 4 characters before the '+' of the floating point value:
          1. First 2 characters: Measurement function (e.g., "DV" for DC voltage)
          2. 3rd character: Can be "O" for overflow
          3. 4th character: Can be "R" for relative measurement
        - Examples: "DV  ", "DVO ", "DVR ", "DVOR"
        - Mantissa: up to 6 digits, first digit max "4", highest value "4.09999"
        - Format: "DV  +4.09999E+0" or "DV  +21.8657E-3"
        """
        response = response.strip()
        
        # Pattern: 2-4 character function code (DV, DVO, DVR, DVOR, etc.) 
        # followed by spaces, then the floating point value
        # Function code: 2 chars (function) + optional "O" (overflow) + optional "R" (relative)
        # Example: "DV  +4.09999E+0" or "DVO +4.09999E+0" or "DVR +4.09999E+0"
        match = re.match(r'^([A-Z]{2}[OR ]{0,2})\s+([+-]?\d+\.?\d*E[+-]?\d+)', response)
        if match:
            function_code = match.group(1)
            value_str = match.group(2)
            
            # Check for overflow indicator
            if 'O' in function_code:
                print(f"  Warning: Overflow detected in Iwatsu response (function code: {function_code})")
            
            # Check for relative measurement
            if 'R' in function_code:
                print(f"  Note: Relative measurement mode (function code: {function_code})")
            
            return float(value_str)
        else:
            # Fallback: Try simpler pattern
            match = re.search(r'([+-]?\d+\.?\d*E[+-]?\d+)', response)
            if match:
                return float(match.group(1))
            raise ValueError(f"Could not parse Iwatsu response: {response}")
    
    def trigger_measurement(self):
        """Trigger measurement using 'G' command.
        
        In X0 (Hold) mode, "G" performs one measurement.
        The measurement completes when SRQ is set (if AE1 is enabled).
        After measurement, CPLT bit in status byte is set until "G" or DC clears it.
        """
        try:
            # Send "G" command to trigger measurement (in X0 Hold mode)
            self.dmm.write("G")
            time.sleep(0.05)  # Small delay for command to be processed
            
            # Wait for SRQ signal (if AE1 is enabled, SRQ indicates measurement END)
            try:
                if hasattr(self.dmm, 'wait_for_srq'):
                    self.dmm.wait_for_srq(timeout=5000)
                else:
                    import pyvisa.constants
                    self.dmm.wait_on_event(pyvisa.constants.EventType.service_request, timeout=5000)
            except (AttributeError, ImportError):
                # Fallback: poll status byte until SRQ bit (bit 6) or CPLT bit (bit 1) is set
                timeout = 5.0
                start_time = time.time()
                while (time.time() - start_time) < timeout:
                    try:
                        stb = self.dmm.read_stb()
                        if stb is not None:
                            # Check for SRQ (bit 6) or CPLT (bit 1) - measurement complete
                            if (stb & 0x40) or (stb & 0x02):  # SRQ or CPLT
                                break
                    except:
                        pass
                    time.sleep(0.01)
            
            # Read the measurement value
            return self.dmm.read().strip()
        except Exception as e:
            # If SRQ wait fails, try reading anyway
            try:
                return self.dmm.read().strip()
            except:
                # Last resort: try query
                try:
                    return self.dmm.query("G").strip()
                except:
                    raise ValueError(f"Could not read measurement from Iwatsu VOAC 7513: {e}")


class Prema6000(LegacyInstrument):
    """Prema 6000 legacy DMM
    
    NOTE: Prema-specific GPIB behavior:
    - "*IDN?" does NOT work! Prema interprets "ID" as DC current mode, not identification query
    - After detection, send "VDS0L1" to configure: VD=DC Volts, S0=continuous, L1=long format
    - L1 = long answer format (with status codes: VDR3A1T3S0Q0MOP0D0B0)
    - L0 = short answer format (value only: +1.987944E+1)
    - Device Clear (DC) resets error states
    - S1 triggers single measurement, S0 for continuous
    - Q1 enables SRQ (Service Request) signaling
    - Status byte (STB) bits: bit 0=new value, bit 6=SRQ
    - "NO VALUE" response indicates error state (needs Device Clear)
    - These behaviors are Prema-specific and may not apply to other instruments like Iwatsu
    """
    
    def __init__(self, dmm_resource):
        super().__init__("Prema 6000", dmm_resource)
        # Perform Device Clear (DC) on initialization - hardware signal through VISA driver
        try:
            self.dmm.clear()  # Device Clear - hardware signal
            print("Device Clear (DC) performed on Prema 6000")
            time.sleep(1)
            
            # Set range to 20V (R3) and autorange OFF (A0)
            self.dmm.write("R3")
            time.sleep(0.5)
            
            # Read response to verify range is set correctly
            # Prema responds with: +01.98785E+1VDR3A0T2S0Q0MOP0D0B0
            # Where R3 confirms 20V range, A0 confirms autorange OFF
            try:
                response = self.dmm.read().strip()
                if response:
                    print(f"  Response after R3: '{response}'")
                    # Check if R3 (20V range) is in the response
                    if 'R3' in response:
                        print("  ✓ Range confirmed: R3 (20V)")
                    # Check if A0 (autorange OFF) is in the response
                    if 'A0' in response:
                        print("  ✓ Autorange confirmed: A0 (OFF)")
                    else:
                        print("  ⚠ Warning: Autorange may not be OFF (expected A0 in response)")
            except Exception as read_err:
                print(f"  Could not read response after R3: {read_err}")
            
            print("  Autorange OFF and range set to 20V (R3)")
        except Exception as e:
            print(f"Warning: Could not perform Device Clear or set range: {e}")
    
    def configure_dc_volts(self):
        """Configure Prema for DC volts measurement.
        From user description: 
        - 'VD' command sets DC volt mode
        - 'VDA1' sets DC volt mode and autoranging
        - 'A0' set autoranging OFF
        - 'R3' set range to 20V
        - 'Q1' sets the SRQ (Service Request Queue)
        - 'S1' triggers single measurement, 'S0' for continuous
        - 'T1' sets the integration time to 100 milliseconds (5.5 digits)
        - 'T2' sets the integration time to 1 seconds (5.5 digits)
        - 'T3' sets the integration time to 1 seconds (6.5 digits)
        - 'T4' sets the integration time to 10 seconds (6.5 digits)
        - 'L1' sets long answer format (with status codes), 'L0' sets short format (value only)"""
        try:
            # VD sets DC volts mode
            # R3 sets fixed range to 20V (autorange OFF, A0)
            # Q1 sets the SRQ (Service Request Queue) - flags when measurement is ready
            # S1 sets single measurement trigger mode (we trigger each measurement with S1)
            # T3 sets the integration time to 1 seconds (6.5 digits)
            # L1 sets long answer format (with all status codes: VDR3A1T3S0Q0MOP0D0B0)
            # The prema 6000 flags with the SRQ when the next measurement is ready! This is important for the animation to work correctly.
            # Autorange (A1) did not choose the best range reliably, so we use fixed range R3 (20V) with autorange OFF (A0)
            # R3 is set in __init__ after Device Clear, so here we just configure the rest
            self.dmm.write("VDR3Q1T3S1L1")
            time.sleep(0.2)
            print("Prema configured for DC volts measurement with 20V range (R3), autorange OFF (A0), SRQ, 1s integration time, single trigger mode, and long format (VDR3Q1T3S1L1)")
            return True
        except Exception as e:
            print(f"Error configuring Prema: {e}")
            return False
    
    def parse_measurement(self, response):
        """Parse Prema response format: '+000002.1E-7IDR2A0T2S0Q0MOP0D0B0'
        The value is at the start, followed by status codes"""
        response = response.strip()
        # Pattern: number at the start, followed by alphanumeric status codes
        # Example: "+000002.1E-7IDR2A0T2S0Q0MOP0D0B0" or "-0001.192E-2VDR2A0T2S0Q0MOP0D0B0"
        # Extract the numeric part at the beginning
        match = re.match(r'([+-]?\d+\.?\d*E[+-]?\d+)', response)
        if match:
            value_str = match.group(1)
            return float(value_str)
        else:
            # Try alternative pattern without E notation
            match = re.match(r'([+-]?\d+\.?\d*)', response)
            if match:
                return float(match.group(1))
            raise ValueError(f"Could not parse Prema response: {response}")
    
    def trigger_measurement(self):
        """Trigger measurement - Send S1 to trigger, wait for SRQ signal, then read measurement value
        With Q1 enabled, Prema 6000 signals via SRQ when measurement is ready.
        S1 triggers a single measurement (S0 would be continuous, but we use S1 for control).
        Similar to Keithley's INIT + FETCH? - we send S1, then wait for SRQ."""
        try:
            # Step 1: Trigger a measurement by sending S1 (single measurement trigger)
            # Note: We configure with S1 each time to ensure we trigger a new measurement
            # The configuration "VDR3Q1T3S1" means: DC Volts, 20V range (R3), Q1 (SRQ), T3 (1s integration), S1 (single trigger)
            self.dmm.write("S1")  # Trigger single measurement
            time.sleep(0.05)  # Small delay for command to be processed
            
            # Step 2: Wait for Service Request (SRQ) signal from Prema 6000
            # This is equivalent to Keithley's FETCH? - it waits for measurement completion
            # Timeout set to 5 seconds (should be enough for 1 second integration time)
            try:
                # Method 1: Use wait_for_srq if available (most compatible, similar to FETCH? blocking behavior)
                if hasattr(self.dmm, 'wait_for_srq'):
                    self.dmm.wait_for_srq(timeout=5000)
                else:
                    # Method 2: Use wait_on_event if available
                    import pyvisa.constants
                    self.dmm.wait_on_event(pyvisa.constants.EventType.service_request, timeout=5000)
            except (AttributeError, ImportError):
                # Method 3: Fallback - poll status byte until SRQ bit is set (bit 6 = 64)
                # This mimics the blocking wait behavior of FETCH?
                timeout = 5.0  # 5 seconds timeout
                start_time = time.time()
                while (time.time() - start_time) < timeout:
                    try:
                        stb = self.dmm.read_stb()  # Read status byte
                        if stb & 64:  # SRQ bit (bit 6) is set
                            break
                    except:
                        pass
                    time.sleep(0.01)  # Small delay to avoid busy loop
            
            # Step 3: Read the measurement value (similar to FETCH? returning the value)
            # Prema outputs measurement when SRQ is triggered
            return self.dmm.read().strip()
        except Exception as e:
            # If SRQ wait fails, try reading anyway (fallback for compatibility)
            try:
                return self.dmm.read().strip()
            except:
                # Last resort: try query
                try:
                    return self.dmm.query("").strip()
                except:
                    raise ValueError(f"Could not read measurement from Prema 6000: {e}")
    
    def test_gpib_interface(self):
        """Test and explore Prema 6000 GPIB interface:
        - Device Clear (DC)
        - Read Status Byte (STB)
        - Test S1 (single measurement) and S0 (continuous)
        - Decode status register bits"""
        print("\n" + "="*60)
        print("PREMA 6000 GPIB INTERFACE TEST")
        print("="*60)
        
        # Test 1: Device Clear (DC)
        print("\n1. Testing Device Clear (DC)...")
        try:
            self.dmm.clear()  # Hardware signal through VISA driver
            time.sleep(0.2)
            print("   ✓ Device Clear executed")
        except Exception as e:
            print(f"   ✗ Device Clear failed: {e}")
        
        # Test 2: Read Status Byte (STB)
        print("\n2. Reading Status Byte (STB)...")
        try:
            stb = self.dmm.read_stb()  # Read status byte
            stb_hex = hex(stb) if stb is not None else None
            stb_decimal = stb if stb is not None else None
            print(f"   STB: {stb_hex}, ({stb_decimal})")
            
            if stb is not None:
                # Decode status register bits according to manual:
                # bit 0 = End measurement (new value)
                # bit 2 = Range Overflow
                # bit 3 = Error messages
                # bit 5 = Reset was done
                # bit 6 = SRQ (Service Request)
                print(f"   Status register bits:")
                print(f"     Bit 0 (End measurement/new value): {bool(stb & 0x01)}")
                print(f"     Bit 1: {bool(stb & 0x02)}")
                print(f"     Bit 2 (Range Overflow): {bool(stb & 0x04)}")
                print(f"     Bit 3 (Error messages): {bool(stb & 0x08)}")
                print(f"     Bit 4: {bool(stb & 0x10)}")
                print(f"     Bit 5 (Reset was done): {bool(stb & 0x20)}")
                print(f"     Bit 6 (SRQ): {bool(stb & 0x40)}")
                print(f"     Bit 7: {bool(stb & 0x80)}")
        except Exception as e:
            print(f"   ✗ Failed to read STB: {e}")
        
        # Note: *STB? query is NOT used - on Prema it returns a measurement value, not the status byte!
        # We use PyVISA's read_stb() method directly to read the status byte via GPIB.
        
        # Test 3: Test S1 (single measurement trigger)
        print("\n3. Testing S1 (single measurement trigger)...")
        try:
            # Configure for single measurement
            self.dmm.write("VDR3Q1T3S1")  # S1 = single measurement
            time.sleep(0.3)
            print("   ✓ S1 command sent (single measurement mode)")
            
            # Try to trigger a measurement
            print("   Waiting for measurement...")
            time.sleep(1.5)  # Wait for measurement to complete
            
            # Read STB after S1
            try:
                stb_after_s1 = self.dmm.read_stb()
                print(f"   STB after S1: {hex(stb_after_s1) if stb_after_s1 is not None else None}, ({stb_after_s1})")
            except:
                pass
            
            # Try to read measurement
            try:
                response = self.dmm.read().strip()
                print(f"   Measurement response: {response[:80]}...")
            except Exception as e:
                print(f"   Could not read measurement: {e}")
        except Exception as e:
            print(f"   ✗ S1 test failed: {e}")
        
        # Test 4: Test S0 (continuous measurements) with Q0 (SRQ off) - check status byte and buffer
        print("\n4. Testing S0Q0 (continuous measurements, SRQ off) - status byte and buffer check...")
        print("   In continuous mode with Q0, measurements happen automatically.")
        print("   We can check status byte (bit 0 = new measurement) and read from buffer.")
        try:
            # Configure for continuous measurements with SRQ OFF
            # S0 = continuous (auto-trigger), Q0 = SRQ off (no service request)
            self.dmm.write("VDR3Q0T3S0")  # Continuous mode, SRQ off
            time.sleep(0.3)
            print("   ✓ S0Q0 command sent (continuous measurement mode, SRQ off)")
            
            # Check status byte a few times to see if it changes (bit 0 = new measurement)
            print("   Checking status byte changes (bit 0 = new measurement available)...")
            previous_stb = None
            for i in range(3):
                time.sleep(1.2)  # Wait for measurement to complete (T3 = 1s integration)
                try:
                    stb = self.dmm.read_stb()
                    stb_hex = hex(stb) if stb is not None else None
                    bit0_new_value = bool(stb & 0x01) if stb is not None else None
                    
                    if previous_stb is not None:
                        changed = " (changed)" if stb != previous_stb else " (unchanged)"
                    else:
                        changed = ""
                    
                    print(f"   Check {i+1}: STB = {stb_hex}, ({stb}){changed}")
                    print(f"      Bit 0 (new value): {bit0_new_value}")
                    
                    # Read from buffer - last valid measurement should always be available
                    try:
                        measurement = self.dmm.read().strip()
                        if measurement:
                            try:
                                value = self.parse_measurement(measurement)
                                print(f"      Buffer: {value:.10f} V")
                            except:
                                print(f"      Buffer: {measurement[:60]}...")
                        else:
                            print(f"      Buffer: empty")
                    except Exception as read_err:
                        print(f"      Buffer: read error - {read_err}")
                    
                    previous_stb = stb
                except Exception as stb_err:
                    print(f"   Check {i+1}: Could not read STB: {stb_err}")
            
            print("   ✓ Continuous mode test: Status byte shows new measurements, buffer contains last valid value")
        except Exception as e:
            print(f"   ✗ S0Q0 test failed: {e}")
        
        # Test 5: Test SRQ event handling
        print("\n5. Testing SRQ event handling...")
        try:
            # Configure with Q1 (SRQ enabled) and autorange on
            self.dmm.write("VDR3Q1T3S1")  # Single measurement with SRQ, 20V range
            time.sleep(0.2)
            print("   Configured with Q1 (SRQ enabled)")
            
            # Try to wait for SRQ
            print("   Waiting for SRQ signal...")
            try:
                if hasattr(self.dmm, 'wait_for_srq'):
                    self.dmm.wait_for_srq(timeout=3000)
                    print("   ✓ SRQ received via wait_for_srq()")
                else:
                    import pyvisa.constants
                    self.dmm.wait_on_event(pyvisa.constants.EventType.service_request, timeout=3000)
                    print("   ✓ SRQ received via wait_on_event()")
            except Exception as e:
                print(f"   SRQ wait: {e}")
            
            # Check STB after SRQ
            try:
                stb_srq = self.dmm.read_stb()
                print(f"   STB after SRQ: {hex(stb_srq) if stb_srq is not None else None}, ({stb_srq})")
                if stb_srq is not None:
                    print(f"   SRQ bit (bit 6) set: {bool(stb_srq & 0x40)}")
            except:
                pass
        except Exception as e:
            print(f"   ✗ SRQ test failed: {e}")
        
        # Restore original configuration
        print("\n6. Restoring original configuration (VDA1Q1T3S1L1)...")
        try:
            self.dmm.write("VDA1Q1T3S1L1")
            time.sleep(0.2)
            print("   ✓ Configuration restored")
        except Exception as e:
            print(f"   ✗ Failed to restore: {e}")
        
        print("\n" + "="*60)
        print("GPIB INTERFACE TEST COMPLETE")
        print("="*60 + "\n")


def detect_instrument_type(dmm_resource, idn_response):
    """Detect if instrument is modern (Keithley) or legacy (Iwatsu, Prema, etc.)
    
    Returns:
        tuple: (is_legacy, instrument_object)
        - is_legacy: True if legacy instrument, False if modern
        - instrument_object: LegacyInstrument instance or None for modern
    """
    idn_response = idn_response.strip()
    
    # Modern instruments return identification string like "KEITHLEY INSTRUMENTS INC.,MODEL 2100,..."
    # Legacy instruments return measurement values with status codes
    
    # Check for Iwatsu pattern: "DV  +4.09999E+0" or "DV  +21.8657E-3" or similar
    # Format: 2-4 character function code (DV, DVO, DVR, DVOR) + spaces + floating point value
    # Function code: 2 chars (function) + optional "O" (overflow) + optional "R" (relative)
    if re.match(r'^[A-Z]{2}[OR ]{0,2}\s+[+-]?\d+\.?\d*E[+-]?\d+', idn_response):
        print(f"Detected Iwatsu VOAC 7513 (legacy instrument)")
        return True, IwatsuVOAC7513(dmm_resource)
    
    # Check for Prema "NO VALUE" error response: "NO VALUE    IDR2A1T3S1Q1MOP0D0B0"
    # This indicates Prema 6000 in error state (needs Device Clear)
    if 'NO VALUE' in idn_response.upper() and re.search(r'[A-Z]{2,3}\d+[A-Z]\d+[A-Z]\d+[A-Z]\d+', idn_response):
        print(f"Detected Prema 6000 (legacy instrument) - 'NO VALUE' error response detected")
        return True, Prema6000(dmm_resource)
    
    # Check for Prema pattern: starts with number followed by status codes
    # Pattern: "+000002.1E-7IDR2A0..." or "-0001.192E-2VDR2A0..."
    # The pattern must match: optional sign, digits, optional decimal, digits, E notation, then alphanumeric status codes
    # More flexible pattern to handle various formats
    if re.match(r'^[+-]?\d+\.?\d*E[+-]?\d+[A-Z0-9]', idn_response):
        print(f"Detected Prema 6000 (legacy instrument) - pattern with E notation")
        return True, Prema6000(dmm_resource)
    
    # Check for Prema pattern without E notation but with status codes
    # Pattern: number followed by at least 2 uppercase letters (status codes)
    if re.match(r'^[+-]?\d+\.?\d+[A-Z]{2}[A-Z0-9]', idn_response):
        print(f"Detected Prema 6000 (legacy instrument) - pattern without E notation")
        return True, Prema6000(dmm_resource)
    
    # Also check for pattern starting with number and having status-like codes
    # This catches cases like "+000002.1E-7IDR2A0..." where there might be slight variations
    if re.match(r'^[+-]?\d+[\.\d]*[Ee][+-]?\d+[A-Z]', idn_response):
        print(f"Detected Prema 6000 (legacy instrument) - flexible E notation pattern")
        return True, Prema6000(dmm_resource)
    
    # Modern instrument (Keithley, etc.)
    print(f"Detected modern instrument: {idn_response}")
    return False, None

# Kommandozeilenargumente parsen
def parse_arguments():
    parser = argparse.ArgumentParser(
        description='DMM Scanner for Keithley 2100 (ratio) and legacy instruments (DC volts)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s 10m              # Measure for 10 minutes (auto-detect instrument)
  %(prog)s 2h                # Measure for 2 hours
  %(prog)s "2025-12-01 09:10:00"  # Measure until specific date/time
  %(prog)s legacy 10m        # Force legacy mode, measure for 10 minutes
  %(prog)s legacy "2025-12-06 09:10:00"  # Force legacy mode, measure until date/time
  %(prog)s -h                # Show this help message
        '''
    )
    
    parser.add_argument('mode_or_duration', nargs='?', type=str,
                       help='Measurement mode ("legacy") or duration (e.g., "10m", "2h", "1d") or end time')
    parser.add_argument('duration', nargs='?', type=str,
                       help='Measurement duration (required if mode is "legacy")')
    
    args = parser.parse_args()
    
    # Simple parsing: check if first argument is "legacy"
    force_legacy = False
    duration_str = None
    
    if args.mode_or_duration and args.mode_or_duration.lower() == 'legacy':
        force_legacy = True
        if args.duration:
            duration_str = args.duration
        else:
            # No duration specified with legacy mode - default to tomorrow 8 AM
            duration_str = None
    elif args.mode_or_duration:
        # First argument is duration, not mode
        duration_str = args.mode_or_duration
    
    return argparse.Namespace(duration=duration_str, force_legacy=force_legacy)

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
# Try to get ResourceManager with diagnostic info
print("Initializing VISA ResourceManager...")

# Try different VISA backends to find one that can see GPIB
# IPython/Spyder typically uses Keysight or NI-VISA which can see GPIB
rm = None
backends_to_try = [
    None,  # Default backend (try first)
    '@ni',  # NI-VISA
    '@kt',  # Keysight VISA (most likely for GPIB)
    '@py',  # pyvisa-py (usually doesn't support GPIB)
]

best_backend = None
best_resource_count = 0
gpib_found_any = False

for backend_spec in backends_to_try:
    try:
        if backend_spec:
            print(f"Trying VISA backend: {backend_spec}")
            rm_test = pyvisa.ResourceManager(backend_spec)
        else:
            print("Trying default VISA backend")
            rm_test = pyvisa.ResourceManager()
        
        # Test if this backend can see GPIB resources
        test_resources = list(rm_test.list_resources())
        gpib_found = any('GPIB' in str(res).upper() for res in test_resources)
        
        backend_name = str(rm_test.visalib) if hasattr(rm_test, 'visalib') else str(backend_spec or 'default')
        print(f"  Backend: {backend_name}")
        print(f"  Resources found: {len(test_resources)}, GPIB found: {gpib_found}")
        
        # Prioritize backends that find GPIB, or use the one with most resources
        if gpib_found:
            rm = rm_test
            best_backend = backend_spec
            gpib_found_any = True
            print(f"  ✓ Using this backend (GPIB resources found!)")
            break
        elif len(test_resources) > best_resource_count:
            # Keep track of backend with most resources as fallback
            best_backend = backend_spec
            best_resource_count = len(test_resources)
            if rm is None:  # Use first working backend as fallback
                rm = rm_test
    except Exception as e:
        print(f"  Failed to use backend {backend_spec}: {e}")
        continue

if rm is None:
    print("Warning: Could not initialize any VISA backend, using default")
    rm = pyvisa.ResourceManager()
elif not gpib_found_any and args.force_legacy:
    # If we're in legacy mode but didn't find GPIB, try to use the backend with most resources
    # and also try to manually construct GPIB resource if we know it should exist
    print(f"\nWarning: No GPIB resources found with any backend!")
    print(f"Using backend with most resources ({best_resource_count} resources)")
    if best_backend:
        try:
            rm = pyvisa.ResourceManager(best_backend)
        except:
            pass

backend_info = str(rm.visalib) if hasattr(rm, 'visalib') else 'unknown'
print(f"\nFinal VISA backend: {backend_info}\n")

DMM = None
name_res = None
legacy_instrument = None
is_legacy = False

# Get all available resources - try multiple times in case GPIB needs time to initialize
print("\nScanning for resources...")
all_resources = []
prev_count = 0
for attempt in range(3):
    try:
        resources = list(rm.list_resources())
        all_resources = resources
        current_count = len(resources)
        print(f"Attempt {attempt + 1}: Found {current_count} resource(s)")
        if attempt > 0 and current_count > prev_count:
            print(f"  (More resources found: {current_count} vs {prev_count})")
        if any('GPIB' in str(res).upper() for res in resources):
            print("  ✓ GPIB resource(s) detected!")
            break  # Found GPIB, no need to retry
        prev_count = current_count
        if attempt < 2:
            time.sleep(0.5)  # Small delay before retry
    except Exception as e:
        print(f"Error listing resources (attempt {attempt + 1}): {e}")
        if attempt < 2:
            time.sleep(0.5)

print(f"\nAll available resources ({len(all_resources)}):")
for i, res in enumerate(all_resources, 1):
    res_type = "GPIB" if 'GPIB' in str(res).upper() else "Other"
    print(f"  {i}. {res} [{res_type}]")

# If legacy mode is forced, prioritize GPIB instruments
if args.force_legacy:
    print("Legacy mode enabled - prioritizing GPIB instruments...")
    # Separate GPIB and non-GPIB resources
    gpib_resources = [addr for addr in all_resources if 'GPIB' in addr.upper()]
    non_gpib_resources = [addr for addr in all_resources if 'GPIB' not in addr.upper()]
    
    print(f"Found GPIB instruments in enumeration ({len(gpib_resources)}):")
    for addr in gpib_resources:
        print(f"  {addr}")
    if non_gpib_resources:
        print(f"Non-GPIB instruments ({len(non_gpib_resources)}) will be skipped in legacy mode")
    
    # If no GPIB resources found in enumeration, try common GPIB addresses directly
    # This is a workaround for VISA backends that don't enumerate GPIB properly
    if len(gpib_resources) == 0:
        print("\nNo GPIB resources found in enumeration, trying common GPIB addresses directly...")
        common_gpib_addresses = [
            'GPIB0::7::INSTR',  # Common address from user's IPython output
            'GPIB0::1::INSTR',
            'GPIB0::2::INSTR',
            'GPIB0::3::INSTR',
            'GPIB0::4::INSTR',
            'GPIB0::5::INSTR',
            'GPIB0::6::INSTR',
            'GPIB0::8::INSTR',
            'GPIB0::9::INSTR',
            'GPIB0::10::INSTR',
        ]
        
        # Test each address to see if we can connect
        for test_addr in common_gpib_addresses:
            try:
                print(f"  Testing {test_addr}...", end=' ')
                test_dmm = rm.open_resource(test_addr, timeout=1000)  # Short timeout for testing
                # Try to query IDN to see if it responds
                try:
                    test_response = test_dmm.query('*IDN?', timeout=1000).strip()
                    if test_response and test_response != '*IDN?':
                        print(f"✓ Found! Response: {test_response[:50]}...")
                        gpib_resources.append(test_addr)
                        test_dmm.close()
                        break  # Found one, stop searching
                    else:
                        print("No valid response")
                        test_dmm.close()
                except:
                    print("No response")
                    test_dmm.close()
            except Exception as e:
                print(f"Not available ({type(e).__name__})")
                continue
        
        if gpib_resources:
            print(f"\n✓ Successfully found {len(gpib_resources)} GPIB resource(s) by direct connection!")
    
    # In legacy mode, only check GPIB resources
    resource_list = gpib_resources
    print(f"\nLegacy mode: Checking {len(resource_list)} GPIB resource(s)...\n")
else:
    # Normal mode: check all resources in original order (USB instruments like Keithley will be checked first)
    resource_list = all_resources
    print(f"\nNormal mode: Checking {len(resource_list)} resource(s) in original order...\n")

# Initialize variables for instrument detection
DMM = None
is_legacy = False
legacy_instrument = None

for addr in resource_list:
    legacy_detected = False
    legacy_instrument_obj = None
    try:
        print(f"Trying to connect to {addr}...")
        DMM = rm.open_resource(addr)
        
        # For GPIB resources, use longer timeout (legacy instruments may be slower)
        is_gpib = 'GPIB' in addr.upper()
        if is_gpib:
            DMM.timeout = 5000  # 5 seconds for GPIB/legacy instruments
            print(f"  Set timeout to 5000ms for GPIB connection")
        
        print(f"Connected to {addr}, attempting identification...")
        
        # For GPIB instruments, use legacy-friendly greeting sequence
        if is_gpib:
            # Step 1: Device Clear (DC) to reset state
            try:
                DMM.clear()  # Device Clear - hardware signal through VISA driver
                time.sleep(0.2)
                print("  Device Clear (DC) performed")
            except:
                pass  # Continue even if clear fails
            
            # Step 2: Read Status Byte (STB) - standard GPIB operation
            # We can't interpret it, but we know if we have an instrument (this is standard)
            stb_available = False
            try:
                stb = DMM.read_stb()
                stb_available = (stb is not None)
                print(f"  Status Byte (STB): {hex(stb) if stb is not None else None}, ({stb}) - Instrument present: {stb_available}")
            except:
                print("  Status Byte (STB): Not available")
            
            # Step 3: Probe with "G" command (GET)
            # - Prema: doesn't understand "G", but after DC we might get a long readout (measurement with status codes)
            # - Iwatsu: understands "G" and returns "DV  +21.8657E-3" format
            name_res = None
            try:
                DMM.write("G")  # Send "G" command (GET)
                time.sleep(0.3)  # Give instrument time to respond
                
                # Try to read response
                try:
                    response = DMM.read().strip()
                    if response:
                        print(f"  'G' command response: '{response[:80]}...'")
                        name_res = response  # Use response for identification
                except:
                    # No response to "G" - might be Prema (doesn't understand "G")
                    # But Prema might have sent measurement after DC, try reading again
                    try:
                        response = DMM.read().strip()
                        if response:
                            print(f"  Buffer read after 'G': '{response[:80]}...'")
                            name_res = response
                    except:
                        pass
            except Exception as g_error:
                print(f"  'G' command failed: {g_error}")
            
            # Step 4: Only if we got nothing from "G", try "*IDN?" (higher level format)
            # Legacy DMMs mostly don't understand this, but some might
            if not name_res:
                try:
                    name_res = DMM.query('*IDN?').strip()
                    print(f"  *IDN? response: '{name_res}'")
                except:
                    # *IDN? failed - this is expected for most legacy instruments
                    print(f"  *IDN? not supported (legacy instrument)")
        else:
            # For non-GPIB (USB), use standard *IDN? query
            try:
                name_res = DMM.query('*IDN?').strip()
                print(f"Response from {addr}: '{name_res}'")
            except Exception as idn_error:
                raise idn_error
            
            # Check if Prema 6000 responded with "NO VALUE" (indicates error state)
            if name_res and 'NO VALUE' in name_res.upper():
                print(f"  Warning: Prema 6000 responded with 'NO VALUE' - performing Device Clear...")
                try:
                    DMM.clear()  # Device Clear to reset error state
                    time.sleep(0.5)
                    # Retry reading buffer after Device Clear
                    try:
                        name_res = DMM.read().strip()
                        print(f"  Response after Device Clear: '{name_res}'")
                    except:
                        pass
                except Exception as dc_error:
                    print(f"  Device Clear or retry failed: {dc_error}")
                    # Continue with the "NO VALUE" response - it might still be a valid Prema response
        
        # Only process if we got a valid IDN response (not just the command echoed back)
        if name_res and name_res != '*IDN?':
            print(f"Found instrument @ {addr}: {name_res}")
            
            # If we already detected a legacy instrument directly (e.g., Prema via command test)
            if legacy_detected and legacy_instrument_obj is not None:
                is_legacy = True
                legacy_instrument = legacy_instrument_obj  # Store in outer scope
            else:
                # Detect if this is a legacy instrument using normal detection
                is_legacy, legacy_instrument = detect_instrument_type(DMM, name_res)
            
            # If Prema 6000 was detected, send initial configuration VDS0L1
            # Note: Prema doesn't understand "*IDN?" - it interprets "ID" as DC current mode!
            # After detection, we configure it with VDS0L1 (DC Volts, continuous, long format)
            if is_legacy and isinstance(legacy_instrument, Prema6000):
                try:
                    print("  Configuring Prema 6000 with VDS0L1 (DC Volts, continuous, long format)...")
                    DMM.write("VDS0L1")
                    time.sleep(0.3)
                    print("  ✓ VDS0L1 sent to Prema 6000")
                except Exception as e:
                    print(f"  Warning: Could not send VDS0L1: {e}")
            
            # In legacy mode, we expect a legacy instrument
            if args.force_legacy and not is_legacy:
                print(f"Warning: {addr} is not a legacy instrument, skipping...")
                DMM.close()
                DMM = None
                continue
            
            if is_legacy:
                name_res = legacy_instrument.name  # Use instrument name for display
            print()  # Empty line for readability
            break  # get first valid instrument (DMM and legacy_instrument are now set)
        else:
            print(f"Warning: Invalid response from {addr}: '{name_res}'")
            DMM.close()
            DMM = None
    except Exception as e:
        # Print error for debugging
        print(f"Error connecting to {addr}: {type(e).__name__}: {e}")
        if DMM is not None:
            try:
                DMM.close()
            except:
                pass
        DMM = None
        continue

if DMM is None:
    if args.force_legacy:
        raise RuntimeError("No valid legacy GPIB instrument found! Make sure a GPIB instrument (e.g., GPIB0::7::INSTR) is connected.")
    else:
        raise RuntimeError("No valid instrument found!")

# Setze Timeout für Messungen (länger für 1 Sekunde Integration)
DMM.timeout = 5000  # 5 Sekunden Timeout (sollte ausreichen für 1 Sekunde Integration)

# Configure DMM based on instrument type
if is_legacy:
# Configure legacy instrument for DC volts measurement
    print("Configuring legacy DMM for DC volts measurement...")
    try:
            if legacy_instrument.configure_dc_volts():
                print("Legacy DMM configured for DC volts measurement")
                
                # Run GPIB interface test for Prema 6000
                if isinstance(legacy_instrument, Prema6000):
                    legacy_instrument.test_gpib_interface()
            else:
                print("Warning: Could not configure legacy DMM, trying to continue...")
    except Exception as e:
            print(f"Error configuring legacy DMM: {e}")
            print("Trying to continue with current settings...")
else:
    # Configure modern instrument (Keithley) for ratio measurement
    print("Configuring DMM for ratio measurement...")

    # Clear any previous errors and status
    try:
        DMM.write("*CLS")  # Clear status register
        #DMM.write("*RST")  # Reset instrument to default state (optional, can be removed if not needed)
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
measurements = deque(maxlen=MAX_DATA_POINTS)  # Measurement values (ratio or DC volts)
ppm_deviations = deque(maxlen=MAX_DATA_POINTS)  # Abweichung in ppm

start_time = time.time()
start_datetime = datetime.now()
measurement_stopped = False  # Flag to stop measurements

# Optimized running statistics (avoid recalculating mean every time)
total_measurement_sum = 0.0  # Running sum of all measurements
total_average_measurement = 0.0  # Current mean (updated incrementally)

# Track number of CSV lines written (for immediate writing without file reading)
csv_lines_written = 0

# Measurement type: 'ratio' for Keithley, 'dc_volts' for legacy instruments
measurement_type = 'ratio' if not is_legacy else 'dc_volts'

# Plot update counter (update plot only periodically to avoid blocking measurements)
plot_update_counter = 0
plot_update_interval = 10  # Update plot every 10 measurements (or every second)
last_plot_update_time = time.time()
plot_update_interval_seconds = 1.0  # Update plot every 1 second

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
# Always use the directory where the Python script is located
# This ensures CSV files are always in the same folder as the program
try:
    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # Fallback if __file__ is not available (shouldn't happen in normal execution)
    script_dir = os.getcwd()

# Verify and display the directory that will be used
print(f"CSV files will be saved to: {script_dir}")

csv_filename = f"Driftmessung_{start_datetime.strftime('%Y-%m-%d_%H-%M-%S')}.csv"
csv_full_path = os.path.join(script_dir, csv_filename)
# Always open using the full path to ensure file is in script directory
csv_file = open(csv_full_path, 'w', newline='', encoding='utf-8')
csv_writer = csv.writer(csv_file, delimiter=',')

# Header mit Kommentar schreiben (ohne Anführungszeichen - direkt als Text schreiben)
if measurement_type == 'ratio':
    csv_file.write(f"# Ratio Measurement - {name_res}\n")
    csv_file.write(f"# Main Channel: 100V, Reference: Auto\n")
else:
    csv_file.write(f"# DC Volts Measurement - {name_res}\n")
    csv_file.write(f"# Start: {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}\n")
    csv_file.write(f"# Stop: {stop_datetime.strftime('%Y-%m-%d %H:%M:%S')}\n")
    csv_file.write(f"# File: {csv_full_path}\n")
    csv_file.write("\n")  # Leere Zeile

# CSV header based on measurement type
if measurement_type == 'ratio':
    csv_writer.writerow(['Datum_Zeit', 'Ratiomesswert', 'Gleitender_Mittelwert', 'Gesamtmittelwert'])
else:
    csv_writer.writerow(['Datum_Zeit', 'DC_Volts', 'Gleitender_Mittelwert', 'Gesamtmittelwert'])
    csv_file.flush()  # Sofort schreiben
print(f"CSV file created: {csv_full_path}")

# Messwert einlesen und in float umwandeln
def get_measurement():
    """Get measurement value - handles both modern (ratio) and legacy (DC volts) instruments"""
    # Check if DMM resource is still valid
    if DMM is None:
        return None

    # Check if resource is closed (try to access a property to test)
    try:
        # Quick check if resource is still valid
        _ = DMM.timeout
    except (AttributeError, Exception):
        # Resource is closed or invalid
        return None

    if is_legacy and legacy_instrument:
        # Legacy instrument measurement
        try:
            value = legacy_instrument.get_measurement_value()
            if measurement_type == 'dc_volts':
                print(f"DC Volts: {value:.10f} V", end='\r')
            else:
                print(f"Value: {value:.10f}", end='\r')
            return value
        except Exception as e:
            # Only print error if it's not a session handle error (resource closed)
            if "session handle" not in str(e).lower() and "closed" not in str(e).lower():
                print(f"\n[ERROR] Legacy instrument exception: {e}")
            return None
    else:
        # Modern instrument (Keithley) - ratio measurement
        try:
            # Method 1: Use INIT + FETCH? (recommended for single measurements)
            # INIT starts measurement, FETCH? waits for completion and retrieves result
            # Note: FETCH? already waits, so *OPC is not strictly necessary
            DMM.write("INIT")  # Initiate measurement
            # Optional: *OPC sets the "operation complete" bit, but FETCH? already waits
            # DMM.write("*OPC")  # Not needed if using FETCH?
            wert = DMM.query("FETCH?")  # Wait for measurement to complete and fetch result
            ratio = float(wert)
            print(f"Ratio: {ratio:.10f}", end='\r')  # Kein Newline, überschreibt Zeile
            return ratio
        except Exception as e:
            # Check if resource is closed
            if "session handle" in str(e).lower() or "closed" in str(e).lower():
                return None  # Resource closed, silently return None
            
            # Try alternative method if FETCH? fails
            try:
                # Method 2: Use READ? (does INIT + FETCH in one command)
                wert = DMM.query("READ?")  # READ? = INIT + FETCH? in one command
                ratio = float(wert)
                print(f"Ratio: {ratio:.10f}", end='\r')
                return ratio
            except Exception as e2:
                # Check if resource is closed
                if "session handle" in str(e2).lower() or "closed" in str(e2).lower():
                    return None  # Resource closed, silently return None
                
                # Check for errors in instrument and display them
                error_msg = None
                try:
                    error = DMM.query("SYST:ERR?")
                    if error and "0," not in error and "0,\"" not in error:
                        error_msg = error.strip()
                except:
                    pass
                
                # Display error (don't clear it - user wants to see errors)
                # But skip if resource is closed
                if error_msg and "session handle" not in error_msg.lower():
                    print(f"\n[ERROR] Instrument: {error_msg}")
                    print(f"[ERROR] Exception: {e2}")
                elif "session handle" not in str(e2).lower():
                    print(f"\n[ERROR] Exception: {e2}")
                return None

# Cleanup function to send appropriate commands to legacy instruments before closing GPIB
def cleanup_prema():
    """Send cleanup commands to legacy instruments before closing GPIB
    
    - Prema 6000: Send Q0S0 (disable SRQ, continuous mode)
    - Iwatsu VOAC 7513: Send AE0 (disable SRQ) and X2 (continuous mode) or leave in X0 (Hold)
    """
    global DMM, is_legacy, legacy_instrument
    
    if DMM is not None and is_legacy and legacy_instrument is not None:
        try:
            # Check if it's a Prema 6000
            if isinstance(legacy_instrument, Prema6000):
                print("\nSending Q0S0 to Prema 6000 (disable SRQ, continuous mode)...")
                DMM.write("Q0S0")
                time.sleep(0.2)
                print("✓ Q0S0 sent to Prema 6000")
            # Check if it's an Iwatsu VOAC 7513
            elif isinstance(legacy_instrument, IwatsuVOAC7513):
                print("\nSending AE0 to Iwatsu VOAC 7513 (disable SRQ)...")
                DMM.write("AE0")  # Disable SRQ
                time.sleep(0.1)
                # Optionally set to continuous mode (X2) or leave in Hold (X0)
                # For now, leave in Hold mode (X0) - can be changed if needed
                print("✓ AE0 sent to Iwatsu VOAC 7513 (SRQ disabled, staying in Hold mode)")
        except Exception as e:
            print(f"Warning: Could not send cleanup commands to legacy instrument: {e}")
    
    # Close GPIB connection (remote off)
    if DMM is not None:
        try:
            DMM.close()
            print("✓ GPIB connection closed (remote off)")
        except Exception as e:
            print(f"Warning: Error closing GPIB connection: {e}")

# Update-Funktion für die Animation
def update(frame):
    global measurement_stopped, ani, total_measurement_sum, total_average_measurement
    global plot_update_counter, last_plot_update_time

    # Check if measurement should be stopped
    if measurement_stopped:
        return

    current_time = time.time()

    # Check if we've reached stop time
    if current_time >= stop_time:
        measurement_stopped = True
        print(f"\nStop time reached ({stop_datetime.strftime('%Y-%m-%d %H:%M:%S')}). Stopping measurement...")
        print("Final plot will remain open. Close the window when done viewing.")
        # Stop the animation properly
        try:
            if ani is not None and ani.event_source is not None:
                ani.event_source.stop()
        except:
            pass  # Ignore errors if already stopped
        # Do one final update to show all data
        if len(zeiten) > 0:
            update_plot()
            save_to_csv()  # Finale Speicherung
        # Cleanup Prema 6000 (send Q0S0) before closing
        cleanup_prema()
        return

    # Perform measurement immediately (synchronized with multimeter readiness)
    # The timestamp is set when measurement actually returns
    neuer_wert = get_measurement()

    # Nur gültige Werte hinzufügen
    if neuer_wert is not None:
        # Get timestamp when measurement was actually taken
        aktuelles_datetime = datetime.now()
        aktuelle_zeit = time.time() - start_time
        
        zeiten.append(aktuelle_zeit)
        zeitpunkte.append(aktuelles_datetime)
        measurements.append(neuer_wert)
        
        # Update running statistics (incremental, much faster than recalculating)
        global total_measurement_sum, total_average_measurement
        total_measurement_sum += neuer_wert
        total_average_measurement = total_measurement_sum / len(measurements)
        
        # Berechne Abweichung vom Gesamtmittelwert aller Messungen
        if total_average_measurement != 0:
            deviation_ppm = ((neuer_wert - total_average_measurement) / total_average_measurement) * 1e6
        else:
            deviation_ppm = 0.0
        ppm_deviations.append(deviation_ppm)
        
        # Write to CSV immediately after measurement
        save_to_csv()
        
        # Update plot periodically (not every measurement to avoid blocking)
        global plot_update_counter, last_plot_update_time
        current_time_for_plot = time.time()
        plot_update_counter += 1
        
        # Update plot every N measurements OR every X seconds (whichever comes first)
        if (plot_update_counter >= plot_update_interval or 
            (current_time_for_plot - last_plot_update_time) >= plot_update_interval_seconds):
            update_plot()
            plot_update_counter = 0
            last_plot_update_time = current_time_for_plot
    else:
        # Measurement failed - plot will be updated by next successful measurement
        pass

# Funktion zum Speichern in CSV
def save_to_csv():
    """Speichere aktuelle Daten in CSV - writes immediately on the fly"""
    global csv_writer, csv_file, csv_lines_written, total_average_measurement

    if len(measurements) == 0:
        return

    # Write only new measurements (immediately, without reading file)
    # This ensures data is written on the fly and not lost on failure
    if len(measurements) > csv_lines_written:
        # Calculate moving average only for the new measurement(s)
        window_size = 15
        for i in range(csv_lines_written, len(measurements)):
            # Calculate moving average for this specific measurement
            if i < window_size - 1:
                # Expanding window für erste 14 Punkte
                window_data = list(measurements)[0:i+1]
            else:
                # Fixed window ab Punkt 15
                start_idx = i - window_size + 1
                window_data = list(measurements)[start_idx:i+1]
            
            moving_avg = sum(window_data) / len(window_data)
            
            # Write immediately (on the fly)
            dt_str = zeitpunkte[i].strftime('%Y-%m-%d %H:%M:%S')
            csv_writer.writerow([dt_str, f'{measurements[i]:.10f}', 
                                f'{moving_avg:.10f}', 
                                f'{total_average_measurement:.10f}'])
            csv_lines_written += 1
        
        # Flush immediately to disk (ensures data is written even on crash)
        csv_file.flush()
        # Also force OS to write to disk (extra safety)
        try:
            os.fsync(csv_file.fileno())
        except:
            pass  # If fsync not available, flush is enough

# Helper function to format voltage values to 6.5 digit precision
# For Prema 6000: 6.5 digits means:
# - Below 20.0000: Half digit is the leading "1", show all 6 digits (e.g., 19.99999)
# - At/above 20.0000: Show 6 significant digits (e.g., 25.0791)
def format_6_5_digits(value):
    """Format a voltage value to 6.5 digit precision

    The half digit is the leading "1" when reading is below 20.0000.
    So 19.99999 shows all 6 digits (1 is half digit + 99999 are 5 full digits).
    Maximum resolution is reached at 19.99999 V (6.5 digits).

    Examples:
        25.0791234 -> 25.0791 (6 significant digits, >= 20.0)
        19.99999 -> 19.99999 (all 6 digits, "1" is half digit, < 20.0)
        19.12345 -> 19.12345 (all 6 digits, < 20.0)
        0.0012345 -> 0.0012345 (6 significant digits)
    """
    if value == 0.0:
            return "0.00000"

    # Use scientific notation for very small or very large values
    if abs(value) < 0.001 or abs(value) >= 1000000:
        return f'{value:.6e}'

    abs_value = abs(value)

    # For values below 20.0000: Show all 6 digits (half digit "1" + 5 full digits)
    # The half digit is the leading "1" in "19.99999", so we show all 6 digits
    if 10.0 <= abs_value < 20.0:
        # Values 10.0 to 19.99999: show 1 digit before decimal + 5 after = 6 digits total
        # Example: 19.99999 -> 19.99999 (all 6 digits, "1" is half digit)
        return f'{value:.5f}'
    elif abs_value < 20.0:
        # Values below 10.0: show 6 significant digits
        return f'{value:.6g}'
    else:
        # For values >= 20.0: Show 6 significant digits
        # Example: 25.0791234 -> 25.0791
        return f'{value:.6g}'

# Funktion zum Aktualisieren des Plots (wird sowohl während Updates als auch am Ende aufgerufen)
def update_plot():
    if len(zeiten) == 0:
        return

    # Achsen löschen und neu zeichnen
    global ax2, total_average_measurement

    # First, remove old secondary axis if it exists (for any measurement type)
    if ax2 is not None:
        try:
            ax2.remove()
        except:
            pass
        ax2 = None

    # Clear primary axis
    ax.clear()

    # Konvertiere Zeitpunkte für X-Achse (Minuten seit Start)
    minuten_seit_start = [(dt - start_datetime).total_seconds() / 60.0 
                          for dt in zeitpunkte]

    # Berechne Abweichungen vom Gesamtmittelwert (falls noch nicht berechnet)
    if len(ppm_deviations) < len(measurements):
        # Recalculate deviations if needed (shouldn't happen often)
        ppm_deviations.clear()
        for meas in measurements:
            if total_average_measurement != 0:
                deviation_ppm = ((meas - total_average_measurement) / total_average_measurement) * 1e6
            else:
                deviation_ppm = 0.0
            ppm_deviations.append(deviation_ppm)

    # For legacy instruments (dc_volts), separate left axis (voltage) and right axis (ppm deviations)
    if measurement_type == 'dc_volts':
        # Create secondary Y-axis for ppm deviations (AFTER clearing primary axis)
        ax2 = ax.twinx()
        ax2.clear()  # Clear the newly created secondary axis
        # Ensure ax2 is completely independent - don't share x-axis
        ax2.set_xlim(ax.get_xlim())  # Sync x-limits but keep y-axis independent
        
        # ===== LEFT AXIS: Actual Voltage Values =====
        # Convert measurements to numpy array for DC Volts
        dc_volts_array = list(measurements)
        
        # Plot actual voltage values (blue line with markers)
        ax.plot(minuten_seit_start, dc_volts_array, marker='o', 
                color='blue', markersize=3, linewidth=1.5, label='DC Volts')
        
        # Red solid line: Moving average of voltage values (15 points)
        if len(measurements) > 1:
            window_size = 15
            moving_avg = []
            moving_avg_times = []
            
            for i in range(len(measurements)):
                if i < window_size - 1:
                    window_data = list(measurements)[0:i+1]
                else:
                    start_idx = i - window_size + 1
                    window_data = list(measurements)[start_idx:i+1]
                
                moving_avg.append(sum(window_data) / len(window_data))
                moving_avg_times.append(minuten_seit_start[i])
            
            if len(moving_avg) > 0:
                label_text = f'Moving Avg ({window_size} pts)'
                ax.plot(moving_avg_times, moving_avg, 
                       color='red', linestyle='-', linewidth=2, alpha=0.8, 
                       label=label_text)
        
        # Red dashed line: Total Average (combined with Mean in legend)
        # Format mean value to 6.5 digit precision (6 significant digits)
        mean_str = format_6_5_digits(total_average_measurement)
        ax.axhline(y=total_average_measurement, color='red', linestyle='--', 
                  linewidth=1.5, alpha=0.7, label=f'Total Average: {mean_str} V')
        
        # ===== RIGHT AXIS: SAME DATA but scaled as PPM deviation =====
        # Plot the SAME DC Volts data on right axis, but transformed to ppm scale
        # Following Stack Overflow approach: same data, different units on each axis
        if len(measurements) > 0 and total_average_measurement != 0:
            # Transform the SAME measurements to ppm for right axis
            # ppm = ((V - V_mean) / V_mean) * 1e6
            ppm_array = [((v - total_average_measurement) / total_average_measurement) * 1e6 
                        for v in dc_volts_array]
            
            # Plot the transformed data on right axis (same data, different scale)
            ax2.plot(minuten_seit_start, ppm_array, marker='o', 
                    color='blue', markersize=3, linewidth=1.5, label='Abweichung (ppm)')
            
            # Configure right axis formatting (label will be set later after all axis config)
            ax2.tick_params(axis='y', labelcolor='blue', which='both')
            ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:.2f}'))
    else:
        # For ratio measurements, ensure ax2 is None (not used)
        if ax2 is not None:
            ax2.remove()
            ax2 = None
        # For ratio measurements, plot ppm deviations (original behavior)
        label_text = 'Ratio Deviation'
        ax.plot(minuten_seit_start, list(ppm_deviations), marker='o', 
                color='blue', markersize=3, linewidth=1.5, label=label_text)
        
        # Rote gestrichelte Linie: Gesamtmittelwert (Abweichung = 0)
        ax.axhline(y=0, color='red', linestyle='--', linewidth=1.5, alpha=0.7, label='Total Average')
        
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
                ax.plot(moving_avg_times, moving_avg, 
                       color='red', linestyle='-', linewidth=2, alpha=0.8, 
                       label=label_text)

    # Titel mit Status
    # Calculate remaining time in minutes (updated every plot update)
    # Round to whole minutes
    current_time = datetime.now()
    remaining_time_minutes = round((stop_datetime - current_time).total_seconds() / 60.0)
    if remaining_time_minutes < 0:
        remaining_time_minutes = 0

    if measurement_type == 'ratio':
        title = f'Ratio Deviation @ {name_res}\nMain: 100V, Ref: Auto | Remaining time: {remaining_time_minutes} min'
    else:
        # For legacy instruments, show mean value in title with 6.5 digit precision
        mean_str = format_6_5_digits(total_average_measurement)
        title = f'DC Volts @ {name_res} | Mean: {mean_str} V | Remaining time: {remaining_time_minutes} min'

    if measurement_stopped:
        title += '\n[Measurement finished successfully]'
    ax.set_title(title)
    ax.set_xlabel('Zeit (Minuten)')
    # Y-axis label: voltage for legacy instruments, ppm for ratio measurements
    if measurement_type == 'dc_volts':
        ax.set_ylabel('Spannung (V)')
        ax.yaxis.set_label_position('left')  # Explicitly set left axis label position
    else:
        ax.set_ylabel('Abweichung (ppm)')
    ax.grid(True, alpha=0.3)
    # Legend inline at top center (fixed position, doesn't flip)
    # For legacy instruments, combine legends from both axes
    if ax2 is not None and measurement_type == 'dc_volts' and len(measurements) > 0:
        # Get handles and labels from both axes
        handles1, labels1 = ax.get_legend_handles_labels()
        handles2, labels2 = ax2.get_legend_handles_labels()
        # Combine them
        ax.legend(handles1 + handles2, labels1 + labels2, 
                 loc='upper center', ncol=4, frameon=True, fancybox=True, shadow=True)
    else:
        ax.legend(loc='upper center', ncol=3, frameon=True, fancybox=True, shadow=True)

    # Y-Achse: Formatierung abhängig vom Messungstyp
    if measurement_type == 'dc_volts':
        # For Prema 6000: Use absolute value scaling on LEFT y-axis
        # Format absolute voltage values to 6.5 digit precision (6 significant digits)
        # The formatter receives the absolute tick value 'x' (already absolute voltage, not deviation)
        # and formats it as absolute value - this is the correct approach for Prema 6000
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: format_6_5_digits(x)))
        # Configure right axis label (must be done after all other axis configuration)
        if ax2 is not None and len(measurements) > 0 and total_average_measurement != 0:
            ax2.yaxis.set_label_position('right')
            ax2.set_ylabel('Abweichung (ppm)', color='blue')
    else:
        # For ppm deviations, use decimal format
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:.2f}'))

    # X-Achse: Zeige alle verfügbaren Daten
    if len(minuten_seit_start) > 0:
        x_min = min(minuten_seit_start)
        x_max = max(minuten_seit_start)
        # Avoid identical limits (happens with single data point)
        if x_min == x_max:
            # Add small range around the single point
            x_min = max(0, x_min - 0.1)
            x_max = x_max + 0.1
        ax.set_xlim(x_min, x_max)
        
        # Statistik anzeigen
        if len(ppm_deviations) > 0:
            # Mean of ppm deviations (should be close to 0)
            mean_dev_ppm = sum(ppm_deviations) / len(ppm_deviations)
            max_dev = max(ppm_deviations)
            min_dev = min(ppm_deviations)
            # Use list comprehension to avoid generator deprecation warning
            std_dev = (sum([(x - mean_dev_ppm)**2 for x in ppm_deviations]) / len(ppm_deviations))**0.5
            
            # Zähle fehlgeschlagene Messungen (None-Werte)
            failed_count = len(measurements) - len(ppm_deviations)
            
            # For legacy instruments, show actual mean value and deviations in ppm
            if measurement_type == 'dc_volts':
                # Show actual mean voltage value with 6.5 digit precision and ppm deviations
                mean_value_str = format_6_5_digits(total_average_measurement)
                
                stats_text = (f'Mean Value: {mean_value_str} V\n'
                            f'Mean Dev: {mean_dev_ppm:.3f} ppm\n'
                            f'Max Dev: {max_dev:.3f} ppm\n'
                            f'Min Dev: {min_dev:.3f} ppm\n'
                            f'Std Dev: {std_dev:.3f} ppm\n'
                            f'Points: {len(ppm_deviations)}')
            else:
                # For ratio measurements, show ppm statistics
                stats_text = (f'Mean Dev: {mean_dev_ppm:.3f} ppm\n'
                            f'Max Dev: {max_dev:.3f} ppm\n'
                            f'Min Dev: {min_dev:.3f} ppm\n'
                            f'Std Dev: {std_dev:.3f} ppm\n'
                            f'Points: {len(ppm_deviations)}')
            
            if failed_count > 0:
                stats_text += f'\nFailed: {failed_count}'
            ax.text(0.02, 0.98, stats_text, transform=ax.transAxes,
                   verticalalignment='top', bbox=dict(boxstyle='round', 
                   facecolor='wheat', alpha=0.8), fontsize=9)
            
            # Zeige letzte Fehler an (falls vorhanden)
            # Dies könnte erweitert werden, um Fehler-Liste zu speichern und anzuzeigen

    plt.draw()  # Force redraw

# Plot vorbereiten
fig, ax = plt.subplots(figsize=(17, 5), dpi=100)  # 1700x500 pixels
# Create secondary axis for legacy instruments (will be used if needed)
ax2 = None
# Use shorter interval for better synchronization (100ms)
# The actual measurement is synchronized to exactly 1 second intervals
ani = animation.FuncAnimation(fig, update, interval=100, blit=False, cache_frame_data=False)

# Cleanup-Funktion für ordentliches Schließen
def on_close(event):
    """Handle plot window close event - cleanup Prema and close GPIB"""
    cleanup_prema()
    # Close ResourceManager
    try:
        rm.close()
    except:
        pass

fig.canvas.mpl_connect('close_event', on_close)

# Anzeige starten
measurement_type_str = "ratio" if measurement_type == 'ratio' else "DC volts"
print(f"\nStarting {measurement_type_str} measurement at {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
print("Measurement interval: 1 second")
print("Close the plot window to stop measurement.\n")

try:
    # In IPython/Spyder, plt.show() is non-blocking, so we need to force blocking mode
    # Check if we're in an interactive environment (sys already imported at top)
    if is_ipython or is_spyder:
        # In interactive environments, turn off interactive mode and use blocking show
        plt.ioff()  # Turn off interactive mode to prevent non-blocking behavior
        plt.show(block=True)  # Force blocking mode
    else:
        # In standalone mode, use blocking show to ensure window stays open
        plt.show(block=True)
finally:
    # Sicherstellen, dass Ressourcen geschlossen werden
    # Stop animation first to prevent further callbacks
    # Use globals() to avoid linter issues with global declaration
    globals()['measurement_stopped'] = True
    
    # Stop the animation properly
    try:
        # Access ani from globals to avoid linter issues
        ani_obj = globals().get('ani', None)
        if ani_obj is not None:
            if hasattr(ani_obj, 'event_source') and ani_obj.event_source is not None:
                ani_obj.event_source.stop()
            globals()['ani'] = None
    except:
        pass
    
    # Small delay to let any pending callbacks finish
    time.sleep(0.1)
    
    # Finale CSV-Speicherung
    if len(measurements) > 0:
        save_to_csv()
    if csv_file is not None:
        csv_file.close()
        print(f"CSV file saved: {csv_full_path}")
    
    # Cleanup Prema 6000 (send Q0S0) and close GPIB connection
    cleanup_prema()
    DMM = None
    
    # Close ResourceManager
    try:
        rm.close()
    except:
        pass
    
    print("\nMeasurement finished successfully. Resources closed.")

