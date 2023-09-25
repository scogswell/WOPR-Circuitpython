# Circuitpython WOPR demo for UnexpectedMaker WOPR board
# Steven Cogswell September 2023
# 
# More or less a circuitpython implementation of the Arduino WOPR demo by UnexpectedMaker
# https://github.com/UnexpectedMaker/wopr
# By default it will connect to Wifi, set the clock and display the time with rainbow defcon LEDs.  (setup in secrets.py)
# If you push BUT1 it will do a short audio/text demo to show things work 
# If you push BUT2 it will imitate the classic 'WarGames' codebreaking sequence.  
# If you push BUT3 (on the back of the WOPR board) it will do the UnexpectedMaker codebreaking sequence 
# If you push BUT4 (on the back of the WOPR board) it will scroll a secret message 
# During codebreaking sequences you can push BUT2 to abort and go back to the clock 
# Some minor informative messages are output to the serial usb during operation. 
# 
# Does it do everything in the UM Arduino WOPR demo?  No it does not.  
# but it shows how to do things with WOPR in circuitpython: display, audio, buttons, LEDs
#
# Tested with Adafruit CircuitPython 8.2.6 on 2023-09-12; TinyS3 with ESP32S3
# My WOPR has the analog audio shield installed.  
# These Circuitpython libraries are in /lib
# adafruit_bus_device, adafruit_ht16k33, adafruit_debouncer, adafruit_ntp, adafruit_ticks
# Does it work on a TinyPICO?  Dunno don't have a TinyPICO, your mileage may vary. 
#
# WOPR kit available here: 
# https://unexpectedmaker.com/shop.html#!/W-O-P-R-Missile-Launch-Code-Display-Kit-HAXORZ-II/p/578899083/category=154506548 

import time, rtc
import neopixel
import board, digitalio
import tinys3
from adafruit_ht16k33.segments import Seg14x4
from adafruit_debouncer import Debouncer
import pwmio
import wifi, socketpool
import adafruit_ntp
import random
import adafruit_ticks

WOPR_BUTTON_1=board.D2
WOPR_BUTTON_2=board.D3
WOPR_BUTTON_3=board.D7
WOPR_BUTTON_4=board.D6
WOPR_AUDIO_PIN=board.D21
WOPR_DEFCON_LEDS=board.D4

def connect_wifi():
    """
    Setup WiFi connection using ssid/password from secrets
    """
    if wifi.radio.ipv4_address is not None:
        return
    pixel.fill((0,255,255))
    try:
        pixel.fill((0,0,255))
        wopr_text("WIFI CONNECT")
        print("Connecting to %s" % secrets["ssid"])
        wifi.radio.connect(secrets["ssid"], secrets["password"])
        print("Connected to %s!" % secrets["ssid"])
        wopr_text(str(wifi.radio.ipv4_address))
        print("IPv4 address",wifi.radio.ipv4_address)
        time.sleep(0.5)
    # Wi-Fi connectivity fails with error messages, not specific errors, so this except is broad.
    except Exception as e:  # pylint: disable=broad-except
        pixel.fill((255,0,0))
        wopr_text("WiFi ERROR")
        raise
    pixel.fill((0,255,0))

def wopr_text(s, pad=False):
    """
    Convenience function to clear the wopr display and show text.
    If pad is True then string will be padded with spaces to force 
    left-align or cut off extra text.  
    """
    if pad==True:
        s2="{0}            ".format(s)[:12]
        print("Original string was  [{0}]".format(s))
        s=s2
        print("Padded string is now [{0}]".format(s))
    wopr_display.fill(0)
    wopr_display.print(s)
    wopr_display.show()

def format_datetime(datetime):
    """
    Simple pretty-print for a datetime object

    :param datetime: A datetime object 
    """
    # pylint: disable=consider-using-f-string
    return "{:02} {:02} {:02} ".format(
        datetime.tm_hour,
        datetime.tm_min,
        datetime.tm_sec,
    )

def wopr_beep(frequency,beep_time,duty_cycle=0.5, continuous=False):
    """
    The ESP32S3 does not support audiopwmio or audioio.  It's okay,
    we can make beeps and boops with regular pwmio. 

    :param frequency: Frequency of tone in Hz
    :param beep_time: Time for tone to sound in seconds (blocks execution until done)
    :param duty_cycle: duty cycle of pwm expressed as 0.0 - 1.0 
    :param continuous: False: tone stops at end of beep_time. True: tone continues to play after function returns
                        and you will have to stop it yourself. 
    """
    audio.frequency = frequency
    audio.duty_cycle = int(65535 * duty_cycle)  
    time.sleep(beep_time) 
    if continuous==False:
        audio.duty_cycle = 0

def wopr_button_beep(beep_type=1):
    """
    Convenience function to hold two beeps for when buttons are pushed/released
    """
    if beep_type==1:
        wopr_beep(880,0.02,0.5)
    else:
        wopr_beep(120,0.02,0.5)

def wopr_solve_movie():
    """
    WOPR codebreaks the code as seen in the movie 'WarGames' 
    """
    missile_code_movie = ['C','P','E',' ','1','7','0','4',' ','T','K','S']
    solve_order_movie = [7,1,4,6,11,2,5,0,10,9]
    wopr_solve(missile_code_movie, solve_order_movie)


def wopr_solve(solved_code, solved_order):
    """
    WOPR codebreaks the given code in the order provided
    
    :param solved_code: list of characters showing what the solved code should be 
    :param solved_order: list showing order in which characters should get "solved" of code. Characters
                            not included will not cycle in the display
    """

    # Colors for the top-fire LEDs as RGB tuples
    defcon_colors =[ (255,255,255),
                    (255,0,0),
                    (255,255,0),
                    (0,255,0),
                    (0,0,255)]
    # Codes that appear during the "random" display during codebreaking
    codes = ['A','B','C','D','E','F','0','1','2','3','4','5','6','7','8','9','0']

    # ticks (ms) min and max interval that a solution will be "found" (randomly chosen)
    solve_interval_min = 4000
    solve_interval_max = 8000
    solve_interval_multiplier = 1.0

    # Set LEDs off and display blank 
    defconLED.fill((0,0,0))
    wopr_text("")

    solveCount=0
    percent_solved = 0 
    current_solution=[' ',' ',' ',' ',' ',' ',' ',' ',' ',' ',' ',' ']
    while solveCount < len(solved_order):
        # Calculate how long to 'codebreak' before 'solving' next character
        ticks_now = adafruit_ticks.ticks_ms()
        ticks_wait = int(random.randint(solve_interval_min,solve_interval_max)*solve_interval_multiplier)
        ticks_next = adafruit_ticks.ticks_add(ticks_now,ticks_wait)
        # Show random character codebreaking, 'solved' characters don't change 
        while adafruit_ticks.ticks_less(adafruit_ticks.ticks_ms(),ticks_next):
            BUT2_debounce.update()  # Push and release button to abort 
            if BUT2_debounce.fell:
                wopr_text("ABORT")
                wopr_beep(1500,0.5,0.5)
                return
            # random "computer sound" beeps and boops
            wopr_beep(random.randint(90,250),0.05,0.5,continuous=True)
            for i in range(solveCount,len(solved_order)):
                current_solution[solved_order[i]]=codes[random.randint(0,len(codes)-1)]
            current_solution_string = "".join(current_solution)  # join character list into string
            wopr_text(current_solution_string)   # display string on wopr
        # The code with a new character "solved" 
        current_solution[solved_order[solveCount]]=solved_code[solved_order[solveCount]]
        current_solution_string = "".join(current_solution)
        wopr_text(current_solution_string)
        solveCount += 1
        # Calculate perecentage through codebreak so that defcon 4 is lit just before the last character is found
        percent_solved = int((1.0 - solveCount / len(solved_order))*4)+1
        defconLED.fill((0,0,0))
        defconLED[percent_solved]=defcon_colors[percent_solved]
        wopr_beep(1500,0.5,0.5)

    # Flash "broken" code on display 
    defconLED.fill((0,0,0))
    defconLED[0]=defcon_colors[0]
    time.sleep(1)
    for x in range(5):
        defconLED.fill((0,0,0))
        wopr_text("")
        time.sleep(0.5)
        defconLED[0]=defcon_colors[0]
        wopr_text(current_solution_string)
        wopr_beep(1500,0.5,0.5)
    # Flash ominous "Launching" text 
    for x in range(5):
        wopr_text("")
        time.sleep(0.5)
        wopr_text("LAUNCHING ...")
        time.sleep(0.5)

# Neopixel LED setup 
pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.3, auto_write=True, pixel_order=neopixel.RGB)  # Neopixel on TinyS3
defconLED = neopixel.NeoPixel(WOPR_DEFCON_LEDS, 5, brightness=0.5,auto_write=True)  # Five Neopixel on top of WOPR (0 -> 4 is right to left)

# Create a colour wheel index int
color_index = 0

# Turn on the power to the NeoPixel
tinys3.set_pixel_power(True)

# Setup analog pwm to use with the analog audio board
audio = pwmio.PWMOut(WOPR_AUDIO_PIN, duty_cycle=0, frequency=440, variable_frequency=True)

# Setup WOPR segment displays as a group 
i2c = board.I2C()
wopr_display = Seg14x4(i2c, address=(0x70,0x72,0x74), auto_write=False)
wopr_text("HELLO WORLD")

# Setup debounced buttons (two on the front, two on the back  )
BUT1 = digitalio.DigitalInOut(WOPR_BUTTON_1)
BUT1.direction = digitalio.Direction.INPUT
BUT1.pull = digitalio.Pull.UP
BUT1_debounce = Debouncer(BUT1)

BUT2 = digitalio.DigitalInOut(WOPR_BUTTON_2)
BUT2.direction = digitalio.Direction.INPUT
BUT2.pull = digitalio.Pull.UP
BUT2_debounce = Debouncer(BUT2)

BUT3 = digitalio.DigitalInOut(WOPR_BUTTON_3)
BUT3.direction = digitalio.Direction.INPUT
BUT3.pull = digitalio.Pull.UP
BUT3_debounce = Debouncer(BUT3)

BUT4 = digitalio.DigitalInOut(WOPR_BUTTON_4)
BUT4.direction = digitalio.Direction.INPUT
BUT4.pull = digitalio.Pull.UP
BUT4_debounce = Debouncer(BUT4)

# Get WiFi Parameters and timezone 
try:
    from secrets import secrets
except ImportError:
    print("WiFi credentials are kept in secrets.py - please add them there!")
    raise
connect_wifi()

# Get local time from NTP server, your time zone offset is 'tz_offset' in secrets.py
# https://github.com/todbot/circuitpython-tricks#set-rtc-time-from-ntp
wopr_text("SET TIME")
pool = socketpool.SocketPool(wifi.radio)
try:
    ntp = adafruit_ntp.NTP(pool, tz_offset=secrets['tz_offset'])
except Exception as e:        
    pixel.fill((255,0,0))
    wopr_text("TIME ERROR")
    raise 
rtc.RTC().datetime = ntp.datetime  
print("current time:", format_datetime(time.localtime()))

# Main forever loop 
while True:
    # Must refresh buttons in the loop for debouncing to work 
    # If a button's status .fell is True, that means the button was released 
    # If a button's status .rose is True, that means the button was pushed 
    BUT1_debounce.update()
    BUT2_debounce.update()
    BUT3_debounce.update()
    BUT4_debounce.update()

    wopr_text(format_datetime(time.localtime()))  # display time on wopr display 

    # Button actions 
    if BUT1_debounce.fell:
        print("Release Button 1")
        wopr_button_beep(2)

    # Show a text and audio demo when BUT1 is released 
    if BUT1_debounce.rose:
        wopr_text("WOPR DEMO")
        wopr_button_beep()
        print("Press Button 1")
        time.sleep(1)

        wopr_text("AUDIO")
        # https://learn.adafruit.com/circuitpython-essentials/circuitpython-pwm
        for f in (262, 294, 330, 349, 392, 440, 494, 523):
                audio.frequency = f
                audio.duty_cycle = 65535 // 2  
                time.sleep(0.05)  
                audio.duty_cycle = 0  # Off
                time.sleep(0.01)
        wopr_text("AUDIO DONE")
        time.sleep(1)

        # Various text and padding options 
        wopr_text("LEFT",pad=True)
        time.sleep(1)
        wopr_text("RIGHT")
        time.sleep(1)
        wopr_text("ABCDEFGHIJKL",pad=True)
        time.sleep(1)
        wopr_text("0123456789AB")
        time.sleep(1)
        wopr_text("WOPR",pad=True)
        time.sleep(1)
        wopr_text("WOPR")
        time.sleep(1)
        wopr_text("TOO LONG MY MAN",pad=True)
        time.sleep(1)
        wopr_text("TOO LONG MY MAN")
        time.sleep(1)

        # Light up all segments of all numbers 
        wopr_display.fill(0)
        for x in range(12):
            wopr_display.set_digit_raw(x,65535)
        wopr_display.show()
        time.sleep(1)

        wopr_text("DEMO OVER")
        time.sleep(1)

    # Do the classic 'WarGames' codebreaking
    if BUT2_debounce.fell:
        wopr_button_beep(2)
        print("Release Button 2")
        wopr_solve_movie()

    if BUT2_debounce.rose:
        wopr_button_beep()
        print("Press Button 2")

    # Do the UnexpectedMaker "LOLZ FOR YOU" codebreak, showing how to use wopr_solve()
    if BUT3_debounce.fell:
        print("Release Button 3")
        wopr_button_beep(2)
        lulz = ['L','O','L','Z',' ','F','O','R',' ','Y','O','U']
        lulz_order=[0,1,2,3,5,6,7,9,10,11]
        wopr_solve(lulz,lulz_order)

    if BUT3_debounce.rose:
        wopr_button_beep()
        print("Press Button 3")

    if BUT4_debounce.fell:
        wopr_button_beep(2)
        print("Release Button 4")

    # Marquee scroll a long piece of text (blocks execution until done )
    if BUT4_debounce.rose:
        wopr_button_beep()
        print("Press Button 4")
        wopr_display.marquee("DON'T FORGET TO DRINK YOUR OVALTINE            ",loop=False)

    # Get the R,G,B values of the next colour for the LEDs
    r,g,b = tinys3.rgb_color_wheel( color_index )
    # Set the colour on the NeoPixel on the TinyS3
    pixel[0] = ( r, g, b, 0.5)
    # Slight rainbow chase on the WOPR defcon LEDs when showing time 
    for i in range(5):
        defconLED[i]=tinys3.rgb_color_wheel(color_index+i*3)
    # Increase the wheel index
    color_index += 1

    # Sleep for 15ms so the colour cycle isn't too fast
    time.sleep(0.015)

