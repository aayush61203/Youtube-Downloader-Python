from flask import Flask, render_template, request, send_file, jsonify, session
import yt_dlp
import os
import uuid
import threading
import time
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor
from werkzeug.utils import secure_filename
from urllib.parse import urlparse

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'youtube-downloader-secret-key-2024')

# Security configurations
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024  # 1MB max request size
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour sessions

# Configuration
DOWNLOAD_FOLDER = 'downloads'
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# Store download progress
download_progress = {}

# Thread pool for concurrent downloads
executor = ThreadPoolExecutor(max_workers=3)

# Security: Rate limiting storage
download_requests = {}
MAX_DOWNLOADS_PER_IP = 5
MAX_DOWNLOADS_PER_HOUR = 20

def is_valid_youtube_url(url):
    """Validate YouTube URL for security"""
    if not url or len(url) > 500:
        return False
    
    youtube_patterns = [
        r'^https?://(www\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]{11}',
        r'^https?://(www\.)?youtu\.be/[a-zA-Z0-9_-]{11}',
        r'^https?://(www\.)?youtube\.com/embed/[a-zA-Z0-9_-]{11}'
    ]
    
    return any(re.match(pattern, url) for pattern in youtube_patterns)

def check_rate_limit(ip_address):
    """Check if IP has exceeded rate limits"""
    current_time = time.time()
    
    if ip_address not in download_requests:
        download_requests[ip_address] = []
    
    # Remove old requests (older than 1 hour)
    download_requests[ip_address] = [
        req_time for req_time in download_requests[ip_address] 
        if current_time - req_time < 3600
    ]
    
    # Check limits
    recent_requests = len(download_requests[ip_address])
    if recent_requests >= MAX_DOWNLOADS_PER_HOUR:
        return False
    
    # Add current request
    download_requests[ip_address].append(current_time)
    return True

class ProgressHook:
    def __init__(self, download_id):
        self.download_id = download_id
        
    def __call__(self, d):
        if d['status'] == 'downloading':
            if 'total_bytes' in d:
                percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
            elif 'total_bytes_estimate' in d:
                percent = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
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
        elif d['status'] == 'error':
            download_progress[self.download_id] = {
                'status': 'error',
                'error': str(d.get('error', 'Unknown error'))
            }

@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' img.youtube.com; img-src 'self' data: img.youtube.com"
    return response

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    # Security: Get client IP
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', '127.0.0.1'))
    
    # Security: Check rate limiting
    if not check_rate_limit(client_ip):
        return jsonify({'error': 'Rate limit exceeded. Please try again later.'}), 429
    
    url = request.form.get('url', '').strip()
    quality = request.form.get('quality', 'best')
    format_ext = request.form.get('format', 'mp4')
    
    # Security: Validate inputs
    if not url:
        return jsonify({'error': 'No URL provided'}), 400
    
    if not is_valid_youtube_url(url):
        return jsonify({'error': 'Invalid YouTube URL'}), 400
    
    # Security: Validate quality and format
    valid_qualities = ['best', 'worst', '1080p', '720p', '480p', '360p', 'audio']
    valid_formats = ['mp4', 'webm', 'mkv', 'mp3', 'wav', 'aac']
    
    if quality not in valid_qualities or format_ext not in valid_formats:
        return jsonify({'error': 'Invalid quality or format'}), 400
    
    # Generate secure download ID
    download_id = hashlib.md5(f"{url}{time.time()}{client_ip}".encode()).hexdigest()[:16]
    
    # Start download using thread pool for better performance
    executor.submit(download_video, url, quality, format_ext, download_id)
    
    return jsonify({'download_id': download_id})

@app.route('/progress/<download_id>')
def get_progress(download_id):
    # Security: Validate download_id format
    if not re.match(r'^[a-f0-9]{16}$', download_id):
        return jsonify({'error': 'Invalid download ID'}), 400
    
    progress = download_progress.get(download_id, {'status': 'not_found'})
    return jsonify(progress)

@app.route('/download_file/<download_id>')
def download_file(download_id):
    # Security: Validate download_id format
    if not re.match(r'^[a-f0-9]{16}$', download_id):
        return jsonify({'error': 'Invalid download ID'}), 400
    
    progress = download_progress.get(download_id)
    if progress and progress.get('status') == 'finished':
        filename = progress.get('filename')
        # Security: Ensure file is in downloads directory
        if filename and os.path.exists(filename) and filename.startswith(DOWNLOAD_FOLDER):
            try:
                return send_file(filename, as_attachment=True)
            except Exception:
                return jsonify({'error': 'File access error'}), 500
    
    return jsonify({'error': 'File not found or download not completed'}), 404

def download_video(url, quality, format_ext, download_id):
    try:
        # Immediate response - start downloading right away
        download_progress[download_id] = {
            'status': 'starting',
            'percent': 0
        }
        
        # Simple filename with download ID to avoid title extraction delays
        output_template = os.path.join(DOWNLOAD_FOLDER, f'video_{download_id}.%(ext)s')
        
        # Advanced anti-bot yt-dlp options - bypass YouTube detection
        ydl_opts = {
            'outtmpl': output_template,
            'progress_hooks': [ProgressHook(download_id)],
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': False,
            'writesubtitles': False,
            'writeautomaticsub': False,
            'writedescription': False,
            'writeinfojson': False,
            'writethumbnail': False,
            'extractaudio': False,
            'concurrent_fragment_downloads': 4,  # Reduce to avoid detection
            'http_chunk_size': 10485760,  # 10MB chunks (smaller)
            'fragment_retries': 2,
            'retries': 3,
            # Advanced anti-bot measures
            'user_agent': 'com.google.android.youtube/17.36.4 (Linux; U; Android 12; GB) gzip',  # YouTube Android app
            'referer': 'https://www.youtube.com/',
            'headers': {
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
                'X-YouTube-Client-Name': '3',  # Android client
                'X-YouTube-Client-Version': '17.36.4',
                'Origin': 'https://www.youtube.com',
            },
            'cookies': None,
            'sleep_interval': 1,  # Add small delay to avoid rate limiting
            'max_sleep_interval': 3,
            # Disable geo-bypass initially (will try in fallback)
            'geo_bypass': False,
            # YouTube-specific extractor arguments - prioritize mobile clients
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'android_embedded'],  # Start with Android clients
                    'player_skip': ['webpage', 'configs'],
                    'comment_sort': ['top'],
                    'max_comments': [0],
                    'include_live_dash': False,
                }
            },
        }
        
        # Super simple format selection - no complex logic
        if quality == 'audio':
            ydl_opts['format'] = 'bestaudio'
            if format_ext == 'mp3':
                ydl_opts['postprocessors'] = [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '128',
                }]
        else:
            # Simple format selection
            if quality == 'best':
                ydl_opts['format'] = 'best'
            elif quality == 'worst':
                ydl_opts['format'] = 'worst'
            else:
                # Just use best available for any specific quality
                ydl_opts['format'] = 'best'
        
        # Multi-tier anti-bot approach
        success = False
        
        # Tier 1: Android client (most reliable for avoiding bot detection)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            success = True
        except Exception as tier1_error:
            error_str = str(tier1_error).lower()
            
            # Tier 2: Try iOS client with different headers
            if 'sign in' in error_str or 'bot' in error_str or 'confirm' in error_str:
                ios_opts = ydl_opts.copy()
                ios_opts.update({
                    'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_6 like Mac OS X) AppleWebKit/605.1.15',
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['ios', 'ios_embedded'],
                            'player_skip': ['webpage'],
                        }
                    },
                    'sleep_interval': 2,  # Slower requests
                })
                
                try:
                    with yt_dlp.YoutubeDL(ios_opts) as ydl:
                        ydl.download([url])
                    success = True
                except Exception as tier2_error:
                    
                    # Tier 3: Try web client with full browser simulation
                    web_opts = {
                        'outtmpl': output_template,
                        'progress_hooks': [ProgressHook(download_id)],
                        'format': 'best',
                        'quiet': True,
                        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                        'headers': {
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                            'Accept-Language': 'en-US,en;q=0.5',
                            'Accept-Encoding': 'gzip, deflate, br',
                            'DNT': '1',
                            'Connection': 'keep-alive',
                            'Upgrade-Insecure-Requests': '1',
                            'Sec-Fetch-Dest': 'document',
                            'Sec-Fetch-Mode': 'navigate',
                            'Sec-Fetch-Site': 'none',
                            'Sec-Fetch-User': '?1',
                        },
                        'extractor_args': {
                            'youtube': {
                                'player_client': ['web'],
                                'player_skip': ['configs'],
                            }
                        },
                        'sleep_interval': 3,
                        'fragment_retries': 5,
                        'retries': 5,
                    }
                    
                    try:
                        with yt_dlp.YoutubeDL(web_opts) as ydl:
                            ydl.download([url])
                        success = True
                    except Exception as tier3_error:
                        
                        # Tier 4: Last resort - try with geo-bypass and minimal detection
                        minimal_opts = {
                            'outtmpl': output_template,
                            'progress_hooks': [ProgressHook(download_id)],
                            'format': 'worst',  # Try worst quality to avoid restrictions
                            'quiet': True,
                            'geo_bypass': True,
                            'geo_bypass_country': 'US',
                            'user_agent': 'yt-dlp/2023.07.06',
                            'sleep_interval': 5,
                            'fragment_retries': 10,
                            'retries': 10,
                        }
                        
                        try:
                            with yt_dlp.YoutubeDL(minimal_opts) as ydl:
                                ydl.download([url])
                            success = True
                        except Exception as final_error:
                            # All tiers failed
                            download_progress[download_id] = {
                                'status': 'error',
                                'error': f"All download methods failed. YouTube may be blocking this content. Original error: {str(tier1_error)}"
                            }
        
    except Exception as e:
        # Handle any completely unexpected errors
        download_progress[download_id] = {
            'status': 'error',
            'error': f"Unexpected error: {str(e)}"
        }

# Removed get_info route - preview is now 100% client-side for instant loading

def extract_video_id(url):
    """Extract video ID from YouTube URL for thumbnail"""
    pattern = r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([a-zA-Z0-9_-]{11})'
    match = re.search(pattern, url)
    return match.group(1) if match else None

def cleanup_old_files():
    """Clean up old downloaded files (older than 1 hour)"""
    try:
        current_time = time.time()
        for filename in os.listdir(DOWNLOAD_FOLDER):
            filepath = os.path.join(DOWNLOAD_FOLDER, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getctime(filepath)
                if file_age > 3600:  # 1 hour
                    os.remove(filepath)
    except Exception:
        pass

if __name__ == '__main__':
    # Cleanup old files on startup
    cleanup_old_files()
    
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    # Production optimizations
    if not debug:
        import logging
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
    
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)