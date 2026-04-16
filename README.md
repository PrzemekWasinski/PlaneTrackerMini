# Plane Tracker Mini

Plane Tracker Mini is a mini ADS-B visualisation device which receives ADS-B radio signals and displays aircraft on a small display by converting the aircraft coordinates received via ADS-B into pixel X and Y value.

Because Plane Tracker Mini is a portable device, the home coordinates cannot be static. To solve this problem a USB GPS module is used to constantly update the home position as it changes.

# Current Prototype:

https://github.com/user-attachments/assets/d295ce6f-7058-49d8-b06d-555ad651d424

The current design features a Raspberry Pi 0, Raspberry Pi USB HAT, Display Hat Mini, Nooelec NESDR RTL-SDR and a Mini 1090Mhz antenna. The program is written in Python and Dump1090 runs in th ebackground for 
decoding ADS-B signals


