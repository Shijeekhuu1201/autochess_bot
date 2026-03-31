import sqlite3
conn = sqlite3.connect(r"C:\Users\Shije\autochess_bot\data\bot.db")
conn.row_factory = sqlite3.Row
print('ACTIONS')
for r in conn.execute("SELECT id, tournament_id, action, status, error_text, created_at FROM tournament_admin_actions ORDER BY id DESC LIMIT 10"):
    print(dict(r))
print('TOURNAMENTS')
for r in conn.execute("SELECT id, title, register_channel_id, register_message_id, waiting_channel_id, waiting_summary_message_id, confirmed_channel_id, confirmed_summary_message_id FROM tournaments ORDER BY id DESC LIMIT 5"):
    print(dict(r))
