#/bin/sh

#download modified torpy version
git clone https://github.com/chrisb09/torpy
#go into torpy dir
cd torpy
#install modified torpy
python3 setup.py install --user
#delete torpy dir
cd ..
rm -r torpy