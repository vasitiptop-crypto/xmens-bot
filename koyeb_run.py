import asyncio
import os
import sys
from dotenv import load_dotenv

# Load env variables from .env if present (mostly for local testing, Koyeb will use console variables)
load_dotenv()

# Add current directory to python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from batch_run import main as run_batch
except ImportError as e:
    print(f"Error importing batch_run: {e}")
    sys.exit(1)

# A basic HTTP response for Koyeb health checks
HTTP_RESPONSE = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/plain\r\n"
    b"Content-Length: 26\r\n"
    b"Connection: close\r\n\r\n"
    b"Telegram Bot is running..."
)

async def handle_client(reader, writer):
    try:
        await reader.read(1024)
        writer.write(HTTP_RESPONSE)
        await writer.drain()
    except Exception as e:
        print(f"Web server error: {e}")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

async def start_web_server():
    # Koyeb passes the port via the PORT env variable (defaults to 8080)
    port = int(os.environ.get("PORT", 8080))
    server = await asyncio.start_server(handle_client, "0.0.0.0", port)
    addr = server.sockets[0].getsockname()
    print(f"Web status server listening on {addr}")
    async with server:
        await server.serve_forever()

async def bot_loop():
    bot_token = os.environ.get("BOT_TOKEN")
    channel_id = os.environ.get("CHANNEL_ID")
    if not bot_token or not channel_id:
        print("WARNING: BOT_TOKEN or CHANNEL_ID not set in environment variables.")
        
    print("Starting background video uploader loop (24/7 continuous checks)...")
    while True:
        try:
            print("\n--- Starting batch upload run ---")
            await run_batch()
            print("--- Batch upload run completed. Sleeping for 10 seconds ---")
        except Exception as e:
            print(f"Error during batch execution: {e}", file=sys.stderr)
            await asyncio.sleep(30) # Sleep longer on error to prevent rapid failures
            continue
            
        await asyncio.sleep(10) # 10 seconds interval for continuous live uploading

async def main():
    # Run status server and uploader loop concurrently
    await asyncio.gather(
        start_web_server(),
        bot_loop()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped by user.")
