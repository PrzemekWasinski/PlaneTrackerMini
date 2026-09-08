# Plane Tracker Mini

Plane Tracker Mini is a mini ADS-B visualisation device which receives ADS-B radio signals and displays aircraft on a small display by converting the aircraft coordinates received via ADS-B into pixel X and Y value. Each plane on the display can be selected by tapping on it to view it's information.

Because Plane Tracker Mini is a portable device, the home coordinates cannot be static. To solve this problem a USB GPS module is used to constantly update the home position as it changes.

# Current Design

<img width="4032" height="3024" alt="planetrackermini-image(1)" src="https://github.com/user-attachments/assets/bcd1a378-efc5-4766-80ab-c3e29a773cc0" />

The current design features a Raspberry Pi 3, Elegoo 3 Inch Touchscreen, Nooelec NESDR RTL-SDR and a Mini 1090Mhz antenna. The program is written in `Python` and `readsb` runs in the background for decoding ADS-B signals.


