import sqlite3
import nest_asyncio
import random as rd
from datetime import datetime
import pytz
import os
from telegram import (
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    Update, 
    ReplyKeyboardMarkup, 
    KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    filters,
)

# Tentukan zona waktu WIB (Asia/Jakarta)
WIB = pytz.timezone("Asia/Jakarta")

# State untuk ConversationHandler (proses edit task)
EDITING = range(1)

# Setup Database SQLite (ditambahkan kolom reminder_time dan notified flag)
def init_db():
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_text TEXT,
            reminder_time TEXT,
            status TEXT DEFAULT 'pending',
            notified INTEGER DEFAULT 0
        )
    """
    )
    conn.commit()
    conn.close()

init_db()

# Fungsi helper untuk menampilkan Home beserta tutorial format chat
async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    greeting_list = [
        "Hai Anetku, chat aja buat tambah task yaa, buat liat list tugas tinggal pilih tombol di bawah yaa bb<3",
        "Makasih sudah menggunakan bot ini, sayang tinggal chat buat tambah task, lihat list task tinggal pilih tombol di bawah yaa sayangku cintaku",
        "Selamat pagi penguasa dunia, biar ga lupa task, jangan lupa cek list task dan chat buat tambah task ya!"
    ]

    tutorial_text = (
        "\n\n📖 **Cara Format Kirim Task:**\n"
        "• Tanggal & Jam:\n`[Task] | dd-mm-yyyy hh:mm` atau `hh.mm`\n"
        "  `Quiz APA | 04-07-2026 14:30`\n"
        "• Tanggal saja:\n`[Task] | dd-mm-yyyy`\n"
        "  `Bayar listrik | 18-09-2026`\n"
        "• Jam saja (Tepat pada waktu yang ditentukan, support `.` atau `:`):\n`[Task] | hh:mm` / `hh.mm`\n"
        "  `Makan sore | 22.58` atau `17:00`\n"
        "• Tanpa waktu:\n`[Task]`\n"
        "  `Beli buah`\n"
    )

    keyboard = [
        [KeyboardButton("📋 All Task(s)"), KeyboardButton("⏳ Pending(s)")],
        [KeyboardButton("🧹 Delete Completed Task(s)"), KeyboardButton("🏠 Home")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        rd.choice(greeting_list) + tutorial_text,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_main_menu(update, context)

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_main_menu(update, context)

# 🔄 Background Worker: Mengecek database setiap 1 menit untuk mengirim reminder otomatis
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    now_wib = datetime.now(WIB).strftime("%d-%m-%Y %H:%M")
    
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    # Ambil task yang statusnya pending, punya waktu reminder, belum dinotifikasi, dan waktunya sudah tiba/lewat
    cursor.execute(
        "SELECT id, user_id, task_text, reminder_time FROM tasks WHERE status = 'pending' AND notified = 0 AND reminder_time <= ?",
        (now_wib,)
    )
    due_tasks = cursor.fetchall()

    for task_id, user_id, task_text, reminder_time in due_tasks:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⏰ **Waktunya Task Ini!**\n\n📌 _{task_text}_\n_(Jadwal: {reminder_time} WIB - Belum diselesaikan, yuk dikerjakan!)_",
                parse_mode="Markdown"
            )
            # Tandai bahwa task ini sudah dikirimi reminder agar tidak spam
            cursor.execute("UPDATE tasks SET notified = 1 WHERE id = ?", (task_id,))
            conn.commit()
        except Exception as e:
            print(f"Gagal mengirim reminder untuk task {task_id}: {e}")

    conn.close()

# Fungsi untuk memparsing format teks task | waktu dengan acuan WIB
def parse_task_input(raw_text):
    if "|" in raw_text:
        parts = raw_text.split("|")
        task_name = parts[0].strip()
        time_str = parts[1].strip().replace(".", ":")  # Ubah titik jadi titik dua agar seragam
        
        now_wib = datetime.now(WIB)
        target_dt = None

        try:
            # Format: dd-mm-yyyy hh:mm
            if len(time_str) == 16:
                target_dt = WIB.localize(datetime.strptime(time_str, "%d-%m-%Y %H:%M"))
            # Format: dd-mm-yyyy (default jam 00:01)
            elif len(time_str) == 10:
                dt_parsed = datetime.strptime(time_str, "%d-%m-%Y")
                target_dt = WIB.localize(datetime.combine(dt_parsed.date(), datetime.min.time())).replace(hour=0, minute=1)
            # Format: hh:mm (hari ini) -> Tepat pada waktu yang diminta dalam WIB
            elif len(time_str) == 5:
                time_parsed = datetime.strptime(time_str, "%H:%M").time()
                target_dt = WIB.localize(datetime.combine(now_wib.date(), time_parsed))
                
                # Jika waktu hari ini sudah terlewat, set untuk besok
                if target_dt < now_wib:
                    target_dt += timedelta(days=1)
        except ValueError:
            pass

        return task_name, target_dt
    else:
        return raw_text.strip(), None

# Tampilkan list task
async def show_task_list(update: Update, context: ContextTypes.DEFAULT_TYPE, filter_status="all"):
    user_id = update.effective_user.id
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    
    if filter_status == "pending":
        cursor.execute("SELECT id, task_text, reminder_time, status FROM tasks WHERE user_id = ? AND status = 'pending'", (user_id,))
        title_msg = "⏳ **Daftar Task Pending(s):**"
    elif filter_status == "completed":
        cursor.execute("SELECT id, task_text, reminder_time, status FROM tasks WHERE user_id = ? AND status = 'completed'", (user_id,))
        title_msg = "✅ **Daftar Task Selesai:**"
    else:
        cursor.execute("SELECT id, task_text, reminder_time, status FROM tasks WHERE user_id = ?", (user_id,))
        title_msg = "📋 **Semua Daftar Task:**"
        
    tasks = cursor.fetchall()
    conn.close()

    if not tasks:
        await update.message.reply_text("Belum ada task yang tersimpan untuk kategori ini.")
        return

    await update.message.reply_text(title_msg, parse_mode="Markdown")

    for task_id, text, reminder_time, status in tasks:
        checkbox = "✅" if status == "completed" else "⬜"
        display_text = f"~{text}~" if status == "completed" else text
        
        if reminder_time:
            display_text += f" ⏱️ `[{reminder_time} WIB]`"

        keyboard = [
            [
                InlineKeyboardButton("✔ Check", callback_data=f"done_{task_id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"edit_{task_id}"),
                InlineKeyboardButton("❌ Hapus", callback_data=f"del_{task_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"{checkbox} {display_text}", reply_markup=reply_markup, parse_mode="Markdown"
        )

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_task_list(update, context, filter_status="all")

async def list_pending_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_task_list(update, context, filter_status="pending")

async def clear_completed_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE user_id = ? AND status = 'completed'", (user_id,))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted_count > 0:
        await update.message.reply_text(f"🧹 Berhasil menghapus {deleted_count} task yang sudah selesai!")
    else:
        await update.message.reply_text("Tidak ada task selesai yang perlu dibersihkan.")

# Handle pesan masuk & parsing task/waktu
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if text == "📋 All Task(s)":
        await list_tasks(update, context)
        return
    elif text == "⏳ Pending(s)":
        await list_pending_tasks(update, context)
        return
    elif text == "🧹 Delete Completed Task(s)":
        await clear_completed_tasks(update, context)
        return
    elif text == "🏠 Home":
        await send_main_menu(update, context)
        return

    # Parsing teks dan waktu
    task_name, target_dt = parse_task_input(text)
    reminder_str = target_dt.strftime("%d-%m-%Y %H:%M") if target_dt else None

    # Simpan ke Database
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tasks (user_id, task_text, reminder_time, status, notified) VALUES (?, ?, ?, 'pending', 0)",
        (user_id, task_name, reminder_str),
    )
    conn.commit()
    conn.close()

    # Konfirmasi ke user
    response_msg = f"✅ Task ditambahkan: _{task_name}_"
    if reminder_str:
        response_msg += f"\n⏰ Reminder diset pada: *{reminder_str} WIB*"

    await update.message.reply_text(response_msg, parse_mode="Markdown")

# Button Handler (Check, Hapus, Edit)
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, task_id = query.data.split("_")
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()

    if action == "done":
        cursor.execute("UPDATE tasks SET status = 'completed' WHERE id = ?",(task_id,))
        conn.commit()
        conn.close()
        await query.edit_message_text(text=f"✅ Task ditandai selesai!", reply_markup=None)
    elif action == "del":
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
        await query.edit_message_text(text=f"🗑️ Task dihapus.", reply_markup=None)
    elif action == "edit":
        context.user_data['editing_task_id'] = task_id
        conn.close()
        await query.message.reply_text("✏️ Silakan kirimkan format baru (bisa dengan `| waktu`) untuk mengganti task ini:")
        return EDITING

async def save_edited_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.get('editing_task_id')
    raw_text = update.message.text

    if task_id:
        task_name, target_dt = parse_task_input(raw_text)
        reminder_str = target_dt.strftime("%d-%m-%Y %H:%M") if target_dt else None

        conn = sqlite3.connect("tasks.db")
        cursor = conn.cursor()
        # Reset notified jadi 0 agar reminder baru bisa terkirim
        cursor.execute("UPDATE tasks SET task_text = ?, reminder_time = ?, notified = 0 WHERE id = ?", (task_name, reminder_str, task_id))
        conn.commit()
        conn.close()

        context.user_data.pop('editing_task_id', None)
        await update.message.reply_text(f"✨ Task berhasil diubah menjadi: _{task_name}_ (Reminder: {reminder_str or 'Tidak ada'} WIB)", parse_mode="Markdown")
    
    return ConversationHandler.END

async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Edit task dibatalkan.")
    return ConversationHandler.END

def main():
    nest_asyncio.apply()

    #with open("token.txt", "r") as f:
        #TOKEN = f.read().strip()
    TOKEN = os.getenv("BOT_TOKEN")
    
    app = ApplicationBuilder().token(TOKEN).build()

    # Daftarkan background job untuk mengecek reminder setiap 60 detik (1 menit)
    if app.job_queue:
        app.job_queue.run_repeating(check_reminders, interval=60, first=5)

    edit_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^edit_")],
        states={
            EDITING: [MessageHandler(filters.TEXT & (~filters.COMMAND), save_edited_task)],
        },
        fallbacks=[CommandHandler("cancel", cancel_edit)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CommandHandler("pending", list_pending_tasks))
    app.add_handler(CommandHandler("clear", clear_completed_tasks))
    
    app.add_handler(edit_handler)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("Bot dengan Background Worker WIB (Pengecekan per 1 menit) sedang berjalan...")
    app.run_polling()

if __name__ == "__main__":
    main()
