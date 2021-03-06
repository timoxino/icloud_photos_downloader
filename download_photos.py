#!/usr/bin/env python
from __future__ import print_function
import click
import os
import socket
import requests
import time
import itertools
import pyicloud
import piexif
from pyicloud.exceptions import PyiCloudAPIResponseError
from tqdm import tqdm
from tzlocal import get_localzone

from authentication import authenticate

# For retrying connection after timeouts and errors
MAX_RETRIES = 5
WAIT_SECONDS = 5


CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])
@click.command(context_settings=CONTEXT_SETTINGS, options_metavar='<options>')
@click.argument('directory', type=click.Path(exists=True), metavar='<directory>')
@click.option('--username',
              help='Your iCloud username or email address',
              metavar='<username>',
              prompt='iCloud username/email')
@click.option('--password',
              help='Your iCloud password '
                   '(default: use PyiCloud keyring or prompt for password)',
              metavar='<password>')
@click.option('--size',
              help='Image size to download (default: original)',
              type=click.Choice(['original', 'medium', 'thumb']),
              default='original')
@click.option('--recent',
              help='Number of recent photos to download (default: download all photos)',
              type=click.IntRange(0))
@click.option('--until-found',
              help='Download most recently added photos until we find x number of previously downloaded consecutive photos (default: download all photos)',
              type=click.IntRange(0))
@click.option('--skip-videos',
              help='Don\'t download any videos (default: Download both photos and videos)',
              is_flag=True)
@click.option('--force-size',
              help='Only download the requested size ' + \
                   '(default: download original if size is not available)',
              is_flag=True)
@click.option('--auto-delete',
              help='Scans the "Recently Deleted" folder and deletes any files found in there. ' + \
                   '(If you restore the photo in iCloud, it will be downloaded again.)',
              is_flag=True)
@click.option('--only-print-filenames',
              help='Only prints the filenames of all files that will be downloaded. ' + \
                '(Does not download any files.)',
              is_flag=True)
@click.option('--folder-structure',
              help='Folder structure (default: {:%Y/%m/%d})',
              metavar='<folder_structure>',
              default='{:%Y/%m/%d}')
@click.option('--set-exif-datetime',
              help='Write the DateTimeOriginal exif tag from file creation date, if it doesn\'t exist.',
              is_flag=True)
@click.option('--smtp-username',
              help='Your SMTP username, for sending email notifications when two-step authentication expires.',
              metavar='<smtp_username>')
@click.option('--smtp-password',
              help='Your SMTP password, for sending email notifications when two-step authentication expires.',
              metavar='<smtp_password>')
@click.option('--smtp-host',
              help='Your SMTP server host. Defaults to: smtp.gmail.com',
              metavar='<smtp_host>',
              default='smtp.gmail.com')
@click.option('--smtp-port',
              help='Your SMTP server port. Default: 587 (Gmail)',
              metavar='<smtp_port>',
              type=click.IntRange(0),
              default=587)
@click.option('--smtp-no-tls',
              help='Pass this flag to disable TLS for SMTP (TLS is required for Gmail)',
              metavar='<smtp_no_tls>',
              is_flag=True)
@click.option('--notification-email',
              help='Email address where you would like to receive email notifications. Default: SMTP username',
              metavar='<notification_email>')


def download(directory, username, password, size, recent, \
    until_found, skip_videos, force_size, auto_delete, \
    only_print_filenames, folder_structure, set_exif_datetime, \
    smtp_username, smtp_password, smtp_host, smtp_port, smtp_no_tls, \
    notification_email):
    """Download all iCloud photos to a local directory"""

    if not notification_email:
        notification_email = smtp_username

    icloud = authenticate(username, password, \
        smtp_username, smtp_password, smtp_host, smtp_port, smtp_no_tls, notification_email)

    if hasattr(directory, 'decode'):
        directory = directory.decode('utf-8')

    directory = os.path.normpath(directory)

    if not only_print_filenames:
        if skip_videos:
            print("Looking up all photos...")
        else:
            print("Looking up all photos and videos...")
    photos = icloud.photos.all
    photos_count = len(photos)

    # Optional: Only download the x most recent photos.
    if recent is not None:
        photos_count = recent
        photos = itertools.islice(photos, recent)

    kwargs = {'total': photos_count}

    if until_found is not None:
        del kwargs['total']
        photos_count = '???'

        # ensure photos iterator doesn't have a known length
        photos = (p for p in photos)

    if not only_print_filenames:
        if not skip_videos:
            print("Downloading %s %s photos and videos to %s/ ..." % (photos_count, size, directory))
        else:
            print("Downloading %s %s photos to %s/ ..." % (photos_count, size, directory))

    consecutive_files_found = 0

    # Not using ASCII characters to fill the meter (it may crash)
    kwargs['ascii'] = True

    if only_print_filenames:
        progress_bar = photos
    else:
        progress_bar = tqdm(photos, **kwargs)

    for photo in progress_bar:
        for _ in range(MAX_RETRIES):
            try:
                if skip_videos \
                    and not photo.item_type == "image":
                    if not only_print_filenames:
                        progress_bar.set_description(
                            "Skipping %s, only downloading photos." % photo.filename)
                    break

                created_date = photo.created.astimezone(get_localzone())

                date_path = (folder_structure).format(created_date)
                download_dir = os.path.join(directory, date_path)

                if not os.path.exists(download_dir):
                    os.makedirs(download_dir)

                download_path = local_download_path(photo, size, download_dir)
                download_path_without_size = local_download_path(photo, None, download_dir)
                # add a check if the "simple" name of the file is found if the size is original
                if os.path.isfile(download_path) or (size =='original' and os.path.isfile(download_path_without_size)):
                    if until_found is not None:
                        consecutive_files_found += 1
                    if not only_print_filenames:
                        progress_bar.set_description("%s already exists." % truncate_middle(download_path, 96))
                    break

                if only_print_filenames:
                    print(download_path)
                else:
                    download_photo(icloud, photo, download_path, size, force_size,
                                   download_dir, progress_bar, only_print_filenames)

                if set_exif_datetime and not only_print_filenames:
                    if photo.filename.lower().endswith(('.jpg', '.jpeg')):
                        if not get_datetime(download_path):
                            set_datetime(download_path, created_date.strftime("%Y:%m:%d %H:%M:%S"))
                    else:
                        timestamp = time.mktime(created_date.timetuple())
                        os.utime(download_path, (timestamp, timestamp))

                if until_found is not None:
                    consecutive_files_found = 0
                break

            except (requests.exceptions.ConnectionError, socket.timeout):
                if not only_print_filenames:
                    tqdm.write('Connection failed, retrying after %d seconds...' % WAIT_SECONDS)
                time.sleep(WAIT_SECONDS)

        else:
            if not only_print_filenames:
                tqdm.write("Could not process %s! Maybe try again later." % photo.filename)

        if until_found is not None and consecutive_files_found >= until_found:
            if not only_print_filenames:
                tqdm.write('Found %d consecutive previusly downloaded photos. Exiting' % until_found)
                progress_bar.close()
            break

    if not only_print_filenames:
        print("All photos have been downloaded!")

    if auto_delete:
        if not only_print_filenames:
            print("Deleting any files found in 'Recently Deleted'...")

        recently_deleted = icloud.photos.albums['Recently Deleted']

        for media in recently_deleted:
            created_date = media.created
            date_path = (folder_structure).format(created_date)
            download_dir = os.path.join(directory, date_path)

            filename = filename_with_size(media, size)
            path = os.path.join(download_dir, filename)

            if os.path.exists(path):
                print("Deleting %s!" % path)
                os.remove(path)

def truncate_middle(s, n):
    if len(s) <= n:
        return s
    n_2 = int(n) // 2 - 2
    n_1 = n - n_2 - 4
    if n_2 < 1: n_2 = 1
    return '{0}...{1}'.format(s[:n_1].encode('utf-8'), s[-n_2:].encode('utf-8'))

def filename_with_size(photo, size):
    return photo.filename.encode('utf-8') \
        .decode('ascii', 'ignore').replace('.', '-%s.' % size)

def filename_without_size(photo):
    return photo.filename.encode('utf-8') \
        .decode('ascii', 'ignore')

def local_download_path(photo, size, download_dir):
    # Strip any non-ascii characters.
    if not size is None:
        filename = filename_with_size(photo, size)
    else:
        filename = filename_without_size(photo)

    download_path = os.path.join(download_dir, filename)

    return download_path


def download_photo(icloud, photo, download_path, size, force_size, download_dir, progress_bar, only_print_filenames):
    truncated_path = truncate_middle(download_path, 96)

    # Fall back to original if requested size is not available
    if size not in photo.versions and not force_size and size != 'original':
        download_photo(icloud, photo, download_path, 'original',
                       True, download_dir, progress_bar, only_print_filenames)
        return

    if not only_print_filenames:
        progress_bar.set_description("Downloading %s" % truncated_path)

    for _ in range(MAX_RETRIES):
        try:
            photo_response = photo.download(size)

            if photo_response:
                with open(download_path, 'wb') as file_obj:
                    for chunk in photo_response.iter_content(chunk_size=1024):
                        if chunk:
                            file_obj.write(chunk)
                break

            elif not only_print_filenames:
                tqdm.write(
                    "Could not find URL to download %s for size %s!" %
                    (photo.filename, size))

        except (requests.exceptions.ConnectionError, socket.timeout, PyiCloudAPIResponseError) as e:
            if e.message == 'Invalid global session':
                if not only_print_filenames:
                    tqdm.write('Session error, re-authenticating...')
                icloud.authenticate()
            else:
                if not only_print_filenames:
                    tqdm.write(
                        '%s download failed, retrying after %d seconds...' %
                        (photo.filename, WAIT_SECONDS))
                time.sleep(WAIT_SECONDS)

        except IOError:
            print("IOError while writing file to %s! "
                "You might have run out of disk space, or the file "
                "might be too large for your OS. "
                "Skipping this file..." % download_path)
            break
    else:
        tqdm.write("Could not download %s! Please try again later." % photo.filename)

def get_datetime(path):
    try:
        exif_dict = piexif.load(path)
        date = exif_dict.get('Exif').get(36867)
    except:
        date = None
    return date

def set_datetime(path, date):
    try:
        exif_dict = piexif.load(path)
        exif_dict.get('1st')[306] = date
        exif_dict.get('Exif')[36867] = date
        exif_dict.get('Exif')[36868] = date
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, path)
    except:
       return

if __name__ == '__main__':
    download()
