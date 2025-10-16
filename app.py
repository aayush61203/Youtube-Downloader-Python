from flask import Flask, render_template, request, send_file, jsonify
import yt_dlp
import os
import threading
import time
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor

# PythonAnywhere fix - specify template folder path
app = Flask(__name__, template_folder='templates')
app.secret_key = 'youtube-downloader-pythonanywhere-2024'

# PythonAnywhere Configuration
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024  # 1MB max request size

# Configuration
DOWNLOAD_FOLDER = 'downloads'
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# Store download progress
download_progress = {}

# Small thread pool for PythonAnywhere
executor = ThreadPoolExecutor(max_workers=1)  # Conservative for free tier

# Rate limiting
download_requests = {}
MAX_DOWNLOADS_PER_HOUR = 10  # Conservative for free tier

def is_valid_youtube_url(url):
    """Validate YouTube URL"""
    if not url or len(url) > 500:
        return False
    
    youtube_patterns = [
        r'^https?://(www\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]{11}',
        r'^https?://(www\.)?youtu\.be/[a-zA-Z0-9_-]{11}',
        r'^https?://(www\.)?youtube\.com/embed/[a-zA-Z0-9_-]{11}'
    ]
    
    return any(re.match(pattern, url) for pattern in youtube_patterns)

def check_rate_limit(ip_address):
    """Check rate limits"""
    current_time = time.time()
    
    if ip_address not in download_requests:
        download_requests[ip_address] = []
    
    # Remove old requests
    download_requests[ip_address] = [
        req_time for req_time in download_requests[ip_address]
        if current_time - req_time < 3600
    ]
    
    if len(download_requests[ip_address]) >= MAX_DOWNLOADS_PER_HOUR:
        return False
    
    download_requests[ip_address].append(current_time)
    return True

class ProgressHook:
    def __init__(self, download_id):
        self.download_id = download_id

    def __call__(self, d):
        if d['status'] == 'downloading':
            if 'total_bytes_estimate' in d and d['total_bytes_estimate']:
                percent = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
            elif 'total_bytes' in d and d['total_bytes']:
                percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
            else:
                percent = 0
            
            download_progress[self.download_id] = {
                'status': 'downloading',
                'percent': round(percent, 1),
                'speed': d.get('speed', 0),
                'eta': d.get('eta', 0)
            }
        elif d['status'] == 'finished':
            download_progress[self.download_id] = {
                'status': 'finished',
                'percent': 100,
                'filename': d['filename']
            }

@app.route('/')
def index():
    try:
        return render_template('index.html')
    except Exception as e:
        # Fallback if template not found
        return f'''
        <h1>YouTube Downloader</h1>
        <p>Template loading issue: {str(e)}</p>
        <p>Please upload the templates folder to PythonAnywhere</p>
        '''

@app.route('/download', methods=['POST'])
def download():
    client_ip = request.environ.get('REMOTE_ADDR', '127.0.0.1')
    
    if not check_rate_limit(client_ip):
        return jsonify({'error': 'Rate limit exceeded. Try again later.'}), 429
    
    url = request.form.get('url', '').strip()
    quality = request.form.get('quality', 'best')
    format_ext = request.form.get('format', 'mp4')
    
    if not url:
        return jsonify({'error': 'No URL provided'}), 400
    
    if not is_valid_youtube_url(url):
        return jsonify({'error': 'Invalid YouTube URL'}), 400
    
    # Generate download ID
    download_id = hashlib.md5(f"{url}{time.time()}".encode()).hexdigest()[:16]
    
    # Start download
    executor.submit(download_video, url, quality, format_ext, download_id)
    
    return jsonify({'download_id': download_id})

@app.route('/progress/<download_id>')
def get_progress(download_id):
    if not re.match(r'^[a-f0-9]{16}$', download_id):
        return jsonify({'error': 'Invalid download ID'}), 400
    
    progress = download_progress.get(download_id, {'status': 'not_found'})
    return jsonify(progress)

@app.route('/download_file/<download_id>')
def download_file(download_id):
    if not re.match(r'^[a-f0-9]{16}$', download_id):
        return jsonify({'error': 'Invalid download ID'}), 400
    
    progress = download_progress.get(download_id)
    if progress and progress.get('status') == 'finished':
        filename = progress.get('filename')
        if filename and os.path.exists(filename):
            try:
                return send_file(filename, as_attachment=True)
            except Exception:
                return jsonify({'error': 'File access error'}), 500
    
    return jsonify({'error': 'File not found'}), 404

def download_video(url, quality, format_ext, download_id):
    try:
        download_progress[download_id] = {'status': 'starting', 'percent': 0}
        
        output_template = os.path.join(DOWNLOAD_FOLDER, f'video_{download_id}.%(ext)s')
        
        # PythonAnywhere optimized settings - UK servers
        ydl_opts = {
            'outtmpl': output_template,
            'progress_hooks': [ProgressHook(download_id)],
            'quiet': True,
            'no_warnings': True,
            'concurrent_fragment_downloads': 1,  # Conservative for free tier
            'http_chunk_size': 524288,  # 512KB chunks
            'fragment_retries': 1,
            'retries': 2,
            # UK-based user agent
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'headers': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-GB,en;q=0.9',  # UK English
                'Accept-Encoding': 'gzip, deflate',
            },
            'socket_timeout': 30,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],  # Simple client for reliability
                    'player_skip': ['webpage'],
                }
            },
        }
        
        # Simple format selection
        if quality == 'audio':
            ydl_opts['format'] = 'bestaudio'
        elif quality == 'worst':
            ydl_opts['format'] = 'worst'
        else:
            ydl_opts['format'] = 'best[height<=480]/best'  # Limit to 480p for speed
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
    except Exception as e:
        download_progress[download_id] = {
            'status': 'error',
            'error': f"Download failed: {str(e)[:100]}"
        }

if __name__ == '__main__':
    app.run(debug=True)