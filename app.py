from flask import Flask, render_template, request, send_file, jsonify, sessionfrom flask import Flask, render_template, request, send_file, jsonify, session

import yt_dlpimport yt_dlp

import osimport os

import uuidimport uuid

import threadingimport threading

import timeimport time

import reimport re

import hashlibimport hashlib

import signalimport signal

from concurrent.futures import ThreadPoolExecutor, TimeoutErrorfrom concurrent.futures import ThreadPoolExecutor, TimeoutError

from werkzeug.utils import secure_filenamefrom werkzeug.utils import secure_filename

from urllib.parse import urlparsefrom urllib.parse import urlparse



app = Flask(__name__)app = Flask(__name__)

app.secret_key = os.environ.get('SECRET_KEY', 'youtube-downloader-secret-key-2024')app.secret_key = os.environ.get('SECRET_KEY', 'youtube-downloader-secret-key-2024')



# Security configurations# Security configurations

app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024  # 1MB max request sizeapp.config['MAX_CONTENT_LENGTH'] = 1024 * 1024  # 1MB max request size

app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 minutesapp.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 minutes for serverless



# Configuration - Railway compatible# Configuration for Vercel Serverless

DOWNLOAD_FOLDER = 'downloads'DOWNLOAD_FOLDER = '/tmp/downloads'  # Vercel's temp directory

if not os.path.exists(DOWNLOAD_FOLDER):if not os.path.exists(DOWNLOAD_FOLDER):

    os.makedirs(DOWNLOAD_FOLDER)    os.makedirs(DOWNLOAD_FOLDER)



# Store download progress# Store download progress

download_progress = {}download_progress = {}



# Optimized thread pool# Optimized for Vercel serverless - smaller thread pool

executor = ThreadPoolExecutor(max_workers=2)executor = ThreadPoolExecutor(max_workers=2)



# Security: Rate limiting storage# Security: Rate limiting storage

download_requests = {}download_requests = {}

MAX_DOWNLOADS_PER_IP = 5MAX_DOWNLOADS_PER_IP = 5

MAX_DOWNLOADS_PER_HOUR = 20MAX_DOWNLOADS_PER_HOUR = 20



def is_valid_youtube_url(url):def is_valid_youtube_url(url):

    """Validate YouTube URL for security"""    """Validate YouTube URL for security"""

    if not url or len(url) > 500:    if not url or len(url) > 500:

        return False        return False

        

    youtube_patterns = [    youtube_patterns = [

        r'^https?://(www\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]{11}',        r'^https?://(www\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]{11}',

        r'^https?://(www\.)?youtu\.be/[a-zA-Z0-9_-]{11}',        r'^https?://(www\.)?youtu\.be/[a-zA-Z0-9_-]{11}',

        r'^https?://(www\.)?youtube\.com/embed/[a-zA-Z0-9_-]{11}'        r'^https?://(www\.)?youtube\.com/embed/[a-zA-Z0-9_-]{11}'

    ]    ]

        

    return any(re.match(pattern, url) for pattern in youtube_patterns)    return any(re.match(pattern, url) for pattern in youtube_patterns)



def check_rate_limit(ip_address):def check_rate_limit(ip_address):

    """Check if IP has exceeded rate limits"""    """Check if IP has exceeded rate limits"""

    current_time = time.time()    current_time = time.time()

        

    if ip_address not in download_requests:    if ip_address not in download_requests:

        download_requests[ip_address] = []        download_requests[ip_address] = []

        

    # Remove old requests (older than 1 hour)    # Remove old requests (older than 1 hour)

    download_requests[ip_address] = [    download_requests[ip_address] = [

        req_time for req_time in download_requests[ip_address]        req_time for req_time in download_requests[ip_address] 

        if current_time - req_time < 3600        if current_time - req_time < 3600

    ]    ]

        

    # Check limits    # Check limits

    if len(download_requests[ip_address]) >= MAX_DOWNLOADS_PER_HOUR:    recent_requests = len(download_requests[ip_address])

        return False    if recent_requests >= MAX_DOWNLOADS_PER_HOUR:

            return False

    # Add current request    

    download_requests[ip_address].append(current_time)    # Add current request

    return True    download_requests[ip_address].append(current_time)

    return True

class ProgressHook:

    def __init__(self, download_id):class ProgressHook:

        self.download_id = download_id    def __init__(self, download_id):

        self.download_id = download_id

    def __call__(self, d):        

        if d['status'] == 'downloading':    def __call__(self, d):

            if 'total_bytes_estimate' in d and d['total_bytes_estimate']:        if d['status'] == 'downloading':

                percent = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100            if 'total_bytes' in d:

            elif 'total_bytes' in d and d['total_bytes']:                percent = (d['downloaded_bytes'] / d['total_bytes']) * 100

                percent = (d['downloaded_bytes'] / d['total_bytes']) * 100            elif 'total_bytes_estimate' in d:

            else:                percent = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100

                percent = 0            else:

                            percent = 0

            download_progress[self.download_id] = {            

                'status': 'downloading',            download_progress[self.download_id] = {

                'percent': round(percent, 1),                'status': 'downloading',

                'speed': d.get('speed', 0),                'percent': round(percent, 1),

                'eta': d.get('eta', 0)                'speed': d.get('speed', 0),

            }                'eta': d.get('eta', 0)

        elif d['status'] == 'finished':            }

            download_progress[self.download_id] = {        elif d['status'] == 'finished':

                'status': 'finished',            download_progress[self.download_id] = {

                'percent': 100,                'status': 'finished',

                'filename': d['filename']                'percent': 100,

            }                'filename': d['filename']

        elif d['status'] == 'error':            }

            download_progress[self.download_id] = {        elif d['status'] == 'error':

                'status': 'error',            download_progress[self.download_id] = {

                'error': str(d.get('error', 'Unknown error'))                'status': 'error',

            }                'error': str(d.get('error', 'Unknown error'))

            }

@app.after_request

def add_security_headers(response):@app.after_request

    """Add security headers to all responses"""def add_security_headers(response):

    response.headers['X-Content-Type-Options'] = 'nosniff'    """Add security headers to all responses"""

    response.headers['X-Frame-Options'] = 'DENY'    response.headers['X-Content-Type-Options'] = 'nosniff'

    response.headers['X-XSS-Protection'] = '1; mode=block'    response.headers['X-Frame-Options'] = 'DENY'

    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'    response.headers['X-XSS-Protection'] = '1; mode=block'

    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' img.youtube.com; img-src 'self' data: img.youtube.com"    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

    return response    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' img.youtube.com; img-src 'self' data: img.youtube.com"

    return response

@app.route('/')

def index():@app.route('/')

    return render_template('index.html')def index():

    return render_template('index.html')

@app.route('/download', methods=['POST'])

def download():@app.route('/download', methods=['POST'])

    # Security: Get client IPdef download():

    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', '127.0.0.1'))    # Security: Get client IP

        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', '127.0.0.1'))

    # Security: Check rate limiting    

    if not check_rate_limit(client_ip):    # Security: Check rate limiting

        return jsonify({'error': 'Rate limit exceeded. Please try again later.'}), 429    if not check_rate_limit(client_ip):

            return jsonify({'error': 'Rate limit exceeded. Please try again later.'}), 429

    url = request.form.get('url', '').strip()    

    quality = request.form.get('quality', 'best')    url = request.form.get('url', '').strip()

    format_ext = request.form.get('format', 'mp4')    quality = request.form.get('quality', 'best')

        format_ext = request.form.get('format', 'mp4')

    # Security: Validate inputs    

    if not url:    # Security: Validate inputs

        return jsonify({'error': 'No URL provided'}), 400    if not url:

            return jsonify({'error': 'No URL provided'}), 400

    if not is_valid_youtube_url(url):    

        return jsonify({'error': 'Invalid YouTube URL'}), 400    if not is_valid_youtube_url(url):

            return jsonify({'error': 'Invalid YouTube URL'}), 400

    # Security: Validate quality and format    

    valid_qualities = ['best', 'worst', '1080p', '720p', '480p', '360p', 'audio']    # Security: Validate quality and format

    valid_formats = ['mp4', 'webm', 'mkv', 'mp3', 'wav', 'aac']    valid_qualities = ['best', 'worst', '1080p', '720p', '480p', '360p', 'audio']

        valid_formats = ['mp4', 'webm', 'mkv', 'mp3', 'wav', 'aac']

    if quality not in valid_qualities or format_ext not in valid_formats:    

        return jsonify({'error': 'Invalid quality or format'}), 400    if quality not in valid_qualities or format_ext not in valid_formats:

            return jsonify({'error': 'Invalid quality or format'}), 400

    # Generate secure download ID    

    download_id = hashlib.md5(f"{url}{time.time()}{client_ip}".encode()).hexdigest()[:16]    # Generate secure download ID

        download_id = hashlib.md5(f"{url}{time.time()}{client_ip}".encode()).hexdigest()[:16]

    # Start download with timeout protection    

    future = executor.submit(download_video, url, quality, format_ext, download_id)    # Start download with timeout protection for free tier

        future = executor.submit(download_video, url, quality, format_ext, download_id)

    return jsonify({'download_id': download_id})    

    # Set a timeout to prevent hanging on free tier (5 minutes max)

@app.route('/progress/<download_id>')    def timeout_handler():

def get_progress(download_id):        time.sleep(300)  # 5 minutes

    # Security: Validate download_id format        if download_id in download_progress and download_progress[download_id].get('status') not in ['finished', 'error']:

    if not re.match(r'^[a-f0-9]{16}$', download_id):            download_progress[download_id] = {

        return jsonify({'error': 'Invalid download ID'}), 400                'status': 'error',

                    'error': 'Download timeout on free tier. Try a shorter video or upgrade to paid plan.'

    progress = download_progress.get(download_id, {'status': 'not_found'})            }

    return jsonify(progress)    

    threading.Thread(target=timeout_handler, daemon=True).start()

@app.route('/download_file/<download_id>')    

def download_file(download_id):    return jsonify({'download_id': download_id})

    # Security: Validate download_id format

    if not re.match(r'^[a-f0-9]{16}$', download_id):@app.route('/progress/<download_id>')

        return jsonify({'error': 'Invalid download ID'}), 400def get_progress(download_id):

        # Security: Validate download_id format

    progress = download_progress.get(download_id)    if not re.match(r'^[a-f0-9]{16}$', download_id):

    if progress and progress.get('status') == 'finished':        return jsonify({'error': 'Invalid download ID'}), 400

        filename = progress.get('filename')    

        # Security: Ensure file is in downloads directory    progress = download_progress.get(download_id, {'status': 'not_found'})

        if filename and os.path.exists(filename) and filename.startswith(DOWNLOAD_FOLDER):    return jsonify(progress)

            try:

                return send_file(filename, as_attachment=True)@app.route('/download_file/<download_id>')

            except Exception:def download_file(download_id):

                return jsonify({'error': 'File access error'}), 500    # Security: Validate download_id format

        if not re.match(r'^[a-f0-9]{16}$', download_id):

    return jsonify({'error': 'File not found or download not completed'}), 404        return jsonify({'error': 'Invalid download ID'}), 400

    

def download_video(url, quality, format_ext, download_id):    progress = download_progress.get(download_id)

    try:    if progress and progress.get('status') == 'finished':

        # Immediate response        filename = progress.get('filename')

        download_progress[download_id] = {        # Security: Ensure file is in downloads directory

            'status': 'starting',        if filename and os.path.exists(filename) and filename.startswith(DOWNLOAD_FOLDER):

            'percent': 0            try:

        }                return send_file(filename, as_attachment=True)

                    except Exception:

        # Simple filename                return jsonify({'error': 'File access error'}), 500

        output_template = os.path.join(DOWNLOAD_FOLDER, f'video_{download_id}.%(ext)s')    

            return jsonify({'error': 'File not found or download not completed'}), 404

        # Railway optimized settings

        ydl_opts = {def download_video(url, quality, format_ext, download_id):

            'outtmpl': output_template,    try:

            'progress_hooks': [ProgressHook(download_id)],        # Immediate response - start downloading right away

            'quiet': True,        download_progress[download_id] = {

            'no_warnings': True,            'status': 'starting',

            'ignoreerrors': False,            'percent': 0

            'writesubtitles': False,        }

            'writeautomaticsub': False,        

            'writedescription': False,        # Simple filename with download ID to avoid title extraction delays

            'writeinfojson': False,        output_template = os.path.join(DOWNLOAD_FOLDER, f'video_{download_id}.%(ext)s')

            'writethumbnail': False,        

            'extractaudio': False,        # Optimized for FREE TIER - minimal resource usage

            'concurrent_fragment_downloads': 2,        ydl_opts = {

            'http_chunk_size': 1048576,  # 1MB chunks            'outtmpl': output_template,

            'fragment_retries': 2,            'progress_hooks': [ProgressHook(download_id)],

            'retries': 2,            'quiet': True,

            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',            'no_warnings': True,

            'headers': {            'ignoreerrors': False,

                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',            'writesubtitles': False,

                'Accept-Language': 'en-US,en;q=0.9',            'writeautomaticsub': False,

                'Accept-Encoding': 'gzip, deflate, br',            'writedescription': False,

                'Connection': 'keep-alive',            'writeinfojson': False,

            },            'writethumbnail': False,

            'socket_timeout': 60,            'extractaudio': False,

            'extractor_args': {            'concurrent_fragment_downloads': 1,  # Single fragment only for low CPU

                'youtube': {            'http_chunk_size': 262144,  # 256KB chunks (tiny for 512MB RAM)

                    'player_client': ['android', 'web'],            'fragment_retries': 1,  # Minimal retries to save CPU

                    'player_skip': ['webpage'],            'retries': 1,  # Don't waste CPU on retries

                }            # Ultra-minimal for free tier - fastest possible

            },            'user_agent': 'yt-dlp/2023.01.06',  # Simple user agent

        }            'referer': None,  # No referer to save bandwidth

                    'headers': {

        # Format selection                'Accept': '*/*',  # Minimal headers

        if quality == 'audio':                'Connection': 'close',  # Close connections to save memory

            ydl_opts['format'] = 'bestaudio'            },

        else:            'cookies': None,

            if quality == 'best':            'sleep_interval': 0,  # No delays - use all available time

                ydl_opts['format'] = 'best[height<=720]/best'            'max_sleep_interval': 0,

            elif quality == 'worst':            'socket_timeout': 60,  # Longer timeout for slow free tier

                ydl_opts['format'] = 'worst'            'geo_bypass': False,  # Disable to save CPU

            else:            # Most minimal extraction possible

                ydl_opts['format'] = 'best[height<=480]/worst'            'extractor_args': {

                        'youtube': {

        # Download                    'player_client': ['android'],  # Single simple client

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:                    'player_skip': ['webpage', 'configs', 'js', 'initial_data'],

            ydl.download([url])                    'skip': ['hls', 'dash', 'live'],  # Skip everything complex

                            }

    except Exception as e:            },

        download_progress[download_id] = {            # Force simplest formats to avoid processing overhead

            'status': 'error',            'format_sort': ['res:480', 'ext:mp4'],  # Prefer smaller files

            'error': f"Download failed: {str(e)[:100]}"        }

        }        

        # FREE TIER OPTIMIZED - force small formats to avoid getting stuck

def cleanup_old_files():        if quality == 'audio':

    """Clean up old downloaded files"""            ydl_opts['format'] = 'worst[acodec!=none]'  # Smallest audio available

    try:            # NO post-processing on free tier - saves CPU and memory

        current_time = time.time()        else:

        for filename in os.listdir(DOWNLOAD_FOLDER):            # Always use small formats on free tier to avoid timeouts

            filepath = os.path.join(DOWNLOAD_FOLDER, filename)            if quality == 'best':

            if os.path.isfile(filepath):                ydl_opts['format'] = 'best[height<=480]/best[height<=720]/best'  # Limit resolution

                file_age = current_time - os.path.getctime(filepath)            elif quality == 'worst':

                if file_age > 3600:  # 1 hour                ydl_opts['format'] = 'worst'

                    os.remove(filepath)            else:

    except Exception:                # Force 480p or lower to ensure completion

        pass                ydl_opts['format'] = 'best[height<=480]/worst'

        

if __name__ == '__main__':        # CLOUD IP BYPASS - Multiple methods for blocked hosting providers

    # Cleanup old files on startup        success = False

    cleanup_old_files()        

            # Method 1: Standard approach with optimized settings

    port = int(os.environ.get('PORT', 5000))        try:

    debug = os.environ.get('DEBUG', 'False').lower() == 'true'            with yt_dlp.YoutubeDL(ydl_opts) as ydl:

                    ydl.download([url])

    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)            success = True
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