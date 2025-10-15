# 🚀 Deploy to Render

## Quick Deploy Steps

1. **Go to [render.com](https://render.com)** and sign up with GitHub
2. **Click "New +" → "Web Service"**
3. **Connect this repository:** `aayush61203/Youtube-Downloader-Python`
4. **Use these settings:**
   ```
   Build Command: pip install -r requirements.txt
   Start Command: gunicorn app:app
   ```
5. **Click "Create Web Service"** - Done! 🎉

## Your app will be live at:
```
https://your-service-name.onrender.com
```

## ⚠️ Free Tier Notes:
- Sleeps after 15 minutes of inactivity
- First request after sleep takes 10-30 seconds
- Perfect for personal use and demos