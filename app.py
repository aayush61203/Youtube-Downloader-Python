from flask import Flask, render_template, request, send_file, jsonify, session
import yt_dlp
import os
import uuid
import threading
import time
import re
import hashlib
import signal
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from werkzeug.utils import secure_filename
from urllib.parse import urlparse

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'youtube-downloader-secret-key-2024')

# Security configurations
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024  # 1MB max request size
app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 minutes for serverless

# Configuration for Vercel Serverless
DOWNLOAD_FOLDER = '/tmp/downloads'  # Vercel's temp directory
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# Store download progress
download_progress = {}

# Optimized for Vercel serverless - smaller thread pool
executor = ThreadPoolExecutor(max_workers=2)

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
    
    # Start download with timeout protection for free tier
    future = executor.submit(download_video, url, quality, format_ext, download_id)
    
    # Set a timeout to prevent hanging on free tier (5 minutes max)
    def timeout_handler():
        time.sleep(300)  # 5 minutes
        if download_id in download_progress and download_progress[download_id].get('status') not in ['finished', 'error']:
            download_progress[download_id] = {
                'status': 'error',
                'error': 'Download timeout on free tier. Try a shorter video or upgrade to paid plan.'
            }
    
    threading.Thread(target=timeout_handler, daemon=True).start()
    
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
        
        # Optimized for FREE TIER - minimal resource usage
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
            'concurrent_fragment_downloads': 1,  # Single fragment only for low CPU
            'http_chunk_size': 262144,  # 256KB chunks (tiny for 512MB RAM)
            'fragment_retries': 1,  # Minimal retries to save CPU
            'retries': 1,  # Don't waste CPU on retries
            # Ultra-minimal for free tier - fastest possible
            'user_agent': 'yt-dlp/2023.01.06',  # Simple user agent
            'referer': None,  # No referer to save bandwidth
            'headers': {
                'Accept': '*/*',  # Minimal headers
                'Connection': 'close',  # Close connections to save memory
            },
            'cookies': None,
            'sleep_interval': 0,  # No delays - use all available time
            'max_sleep_interval': 0,
            'socket_timeout': 60,  # Longer timeout for slow free tier
            'geo_bypass': False,  # Disable to save CPU
            # Most minimal extraction possible
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],  # Single simple client
                    'player_skip': ['webpage', 'configs', 'js', 'initial_data'],
                    'skip': ['hls', 'dash', 'live'],  # Skip everything complex
                }
            },
            # Force simplest formats to avoid processing overhead
            'format_sort': ['res:480', 'ext:mp4'],  # Prefer smaller files
        }
        
        # FREE TIER OPTIMIZED - force small formats to avoid getting stuck
        if quality == 'audio':
            ydl_opts['format'] = 'worst[acodec!=none]'  # Smallest audio available
            # NO post-processing on free tier - saves CPU and memory
        else:
            # Always use small formats on free tier to avoid timeouts
            if quality == 'best':
                ydl_opts['format'] = 'best[height<=480]/best[height<=720]/best'  # Limit resolution
            elif quality == 'worst':
                ydl_opts['format'] = 'worst'
            else:
                # Force 480p or lower to ensure completion
                ydl_opts['format'] = 'best[height<=480]/worst'
        
        # CLOUD IP BYPASS - Multiple methods for blocked hosting providers
        success = False
        
        # Method 1: Standard approach with optimized settings
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            success = True
        except Exception as error:
            error_str = str(error).lower()
            
            # Method 2: Different approach for API errors (400/403)
            if not success and ('400' in error_str or '403' in error_str or 'bad request' in error_str or 'api' in error_str):
                # Try embedded player bypass
                embedded_opts = {
                    'outtmpl': output_template,
                    'progress_hooks': [ProgressHook(download_id)],
                    'format': 'worst',
                    'quiet': True,
                    'user_agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
                    'headers': {
                        'Accept': 'text/html,application/xhtml+xml',
                        'Accept-Language': 'en-US,en;q=0.5',
                        'Connection': 'close',
                    },
                    'socket_timeout': 60,
                    'fragment_retries': 0,
                    'retries': 0,
                    'concurrent_fragment_downloads': 1,
                    'http_chunk_size': 131072,
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['web_embedded'],
                            'player_skip': ['webpage', 'configs', 'js', 'initial_data'],
                        }
                    },
                }
                
                try:
                    with yt_dlp.YoutubeDL(embedded_opts) as ydl:
                        ydl.download([url])
                    success = True
                except Exception as embedded_error:
                    # Method 3: Force generic extractor as last resort
                    generic_opts = {
                        'outtmpl': output_template,
                        'progress_hooks': [ProgressHook(download_id)],
                        'format': 'worst',
                        'quiet': True,
                        'user_agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
                        'socket_timeout': 120,
                        'force_generic_extractor': True,
                        'fragment_retries': 0,
                        'retries': 0,
                    }
                    
                    try:
                        with yt_dlp.YoutubeDL(generic_opts) as ydl:
                            ydl.download([url])
                        success = True
                    except Exception as final_error:
                        download_progress[download_id] = {
                            'status': 'error',
                            'error': f"Cloud hosting IP blocked by YouTube. Video unavailable on free hosting. Try: 1) Different video 2) Paid hosting plan. Error: {str(error)[:100]}"
                        }
            else:
                # Non-API error
                download_progress[download_id] = {
                    'status': 'error',
                    'error': f"Download error: {str(error)[:100]}"
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