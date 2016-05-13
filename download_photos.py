#!/usr/bin/env python

"""
iCloud Photos Downloader

If your account has two-factor authentication enabled,
you will be prompted for a code. 

Note: Both regular login and two-factor authentication will expire after an interval set by Apple, 
at which point you will have to re-authenticate. This interval is currently two months.

Usage:
  download_photos --username=<username> [--password=<password>] <download_directory> 
  download_photos --username=<username> [--password=<password>] <download_directory>
                  [--size=original | --size=medium | --size=thumb]
  download_photos -h | --help
  download_photos --version

Options:
  --username=<username>     iCloud username (or email)
  --password=<password>     iCloud password (optional if saved in keyring)
  --size=<size>             Image size to download [default: original].
  -h --help                 Show this screen.
  --version                 Show version.
"""

import docopt
from schema import Schema, And, Use, Optional, SchemaError
import os
import sys

try:
    arguments = docopt.docopt(__doc__, version='1.0.0')

    sch = Schema({ '<download_directory>': Schema(os.path.isdir,
                        error=('%s is not a valid directory' % arguments['<download_directory>'])),
                    '--size': Schema((lambda s: s in ('original', 'medium', 'thumb')),
                         error='--size must be one of: original, medium, thumb')
                }, ignore_extra_keys=True)
    
    sch.validate(arguments)

except docopt.DocoptExit as e:
    print e.message
    sys.exit(1)

except SchemaError as e:
    print e.message
    sys.exit(1)


import click
from tqdm import tqdm
from dateutil.parser import parse
from pyicloud import PyiCloudService

print("Signing in...")

if '--password' in arguments:
  icloud = PyiCloudService(arguments['--username'], arguments['--password'])
else:
  icloud = PyiCloudService(arguments['--username'])


if icloud.requires_2fa:
    print "Two-factor authentication required. Your trusted devices are:"

    devices = icloud.trusted_devices
    for i, device in enumerate(devices):
        print "  %s: %s" % (i, device.get('deviceName',
            "SMS to %s" % device.get('phoneNumber')))

    device = click.prompt('Which device would you like to use?', default=0)
    device = devices[device]
    if not icloud.send_verification_code(device):
        print "Failed to send verification code"
        sys.exit(1)

    code = click.prompt('Please enter validation code')
    if not icloud.validate_verification_code(device, code):
        print "Failed to verify verification code"
        sys.exit(1)


print("Looking up all photos...")
all_photos = icloud.photos.all
photos_count = len(all_photos.photos)

base_download_dir = arguments['<download_directory>'].rstrip('/')
size = arguments['--size']

print("Downloading %d %s photos to %s/ ..." % (photos_count, size, base_download_dir))

pbar = tqdm(all_photos, total=photos_count)

for photo in pbar:
    created_date = parse(photo.created)
    date_path = '{:%Y/%m/%d}'.format(created_date)
    download_dir = '/'.join((base_download_dir, date_path))

    filename_with_size = photo.filename.replace('.', '-%s.' % size)
    filepath = '/'.join((download_dir, filename_with_size))

    if os.path.isfile(filepath):
        pbar.set_description("%s already exists." % filepath)
        continue

    pbar.set_description("Downloading %s to %s" % (photo.filename, filepath))        

    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    download = photo.download(size)

    if download:
      with open(filepath, 'wb') as file:
          for chunk in download.iter_content(chunk_size=1024): 
              if chunk:
                  file.write(chunk)
    else:
      tqdm.write("Could not download %s!" % photo.filename)


print("All photos have been downloaded!")