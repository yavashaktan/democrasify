import streamlit as st
import sqlite3
import pandas as pd
from streamlit_js_eval import streamlit_js_eval
from streamlit_autorefresh import st_autorefresh

REFRESH_SECS = 30  # Kaç saniyede bir yenilensin?

st.set_page_config(page_title="Demokrasify - Ortak Playlist", page_icon="🎵", layout="centered")

st_autorefresh(interval=REFRESH_SECS * 1000, limit=None, key="autorefresh")

# ── Veritabanı ──────────────────────────────────────────────────────────────

def get_conn():
    return sqlite3.connect('playlist.db', check_same_thread=False)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            artist TEXT,
            added_by TEXT NOT NULL,
            likes INTEGER DEFAULT 0,
            dislikes INTEGER DEFAULT 0,
            status TEXT DEFAULT 'approved'
        )
    ''')
    # Mevcut DB'de status kolonu yoksa ekle, var olanları 'approved' yap
    try:
        c.execute("ALTER TABLE songs ADD COLUMN status TEXT DEFAULT 'approved'")
        c.execute("UPDATE songs SET status = 'approved' WHERE status IS NULL")
    except sqlite3.OperationalError:
        pass  # Kolon zaten var

    c.execute('''
        CREATE TABLE IF NOT EXISTS votes (
            device_id TEXT NOT NULL,
            song_id INTEGER NOT NULL,
            vote_type TEXT NOT NULL,
            PRIMARY KEY (device_id, song_id)
        )
    ''')
    conn.commit()
    conn.close()

def get_songs():
    """Sadece admin onaylı şarkıları döndürür."""
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM songs WHERE status = 'approved'", conn)
    conn.close()
    if not df.empty:
        df['score'] = df['likes'] - df['dislikes']
        df = df.sort_values(by='score', ascending=False).reset_index(drop=True)
    return df

def get_pending_songs():
    """Admin onayı bekleyen şarkıları döndürür."""
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM songs WHERE status = 'pending'", conn)
    conn.close()
    return df

def get_device_votes(device_id: str) -> dict:
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT song_id, vote_type FROM votes WHERE device_id = ?', (device_id,))
    rows = c.fetchall()
    conn.close()
    return {str(r[0]): r[1] for r in rows}

def record_vote(device_id: str, song_id: int, vote_type: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT 1 FROM votes WHERE device_id=? AND song_id=?', (device_id, song_id))
    if c.fetchone():
        conn.close()
        return
    c.execute('INSERT INTO votes (device_id, song_id, vote_type) VALUES (?,?,?)',
              (device_id, song_id, vote_type))
    if vote_type == 'like':
        c.execute('UPDATE songs SET likes = likes + 1 WHERE id = ?', (song_id,))
    else:
        c.execute('UPDATE songs SET dislikes = dislikes + 1 WHERE id = ?', (song_id,))
    conn.commit()
    conn.close()

def add_song(title, artist, added_by):
    """Yeni öneriyi 'pending' statüsüyle ekler."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO songs (title, artist, added_by, status) VALUES (?, ?, ?, 'pending')",
              (title, artist, added_by))
    conn.commit()
    conn.close()

def approve_song(song_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE songs SET status = 'approved' WHERE id = ?", (song_id,))
    conn.commit()
    conn.close()

def reject_song(song_id: int):
    """Reddedilen öneriyi tamamen siler."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM songs WHERE id = ?", (song_id,))
    conn.commit()
    conn.close()

def delete_song(song_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute('DELETE FROM songs WHERE id = ?', (song_id,))
    c.execute('DELETE FROM votes WHERE song_id = ?', (song_id,))
    conn.commit()
    conn.close()

# ── CSS ─────────────────────────────────────────────────────────────────────

st.markdown("""
    <style>
    @media (max-width: 640px) {
        [data-testid="stHorizontalBlock"] {
            flex-wrap: nowrap !important;
            flex-direction: row !important;
            align-items: center !important;
        }
        [data-testid="stHorizontalBlock"] > div:nth-child(1) {
            width: 70% !important;
            min-width: 70% !important;
        }
        [data-testid="stHorizontalBlock"] > div:nth-child(2),
        [data-testid="stHorizontalBlock"] > div:nth-child(3) {
            width: 15% !important;
            min-width: 15% !important;
        }
    }
    .stButton>button {
        width: 100%;
        border-radius: 10px;
        padding: 5px;
    }
    </style>
""", unsafe_allow_html=True)

# ── Başlangıç ───────────────────────────────────────────────────────────────

init_db()

if 'fail_count' not in st.session_state:
    st.session_state.fail_count = 0
if 'admin_logged' not in st.session_state:
    st.session_state.admin_logged = False

device_id = streamlit_js_eval(
    js_expressions="""(function(){
        var id = localStorage.getItem('democrasify_device_id');
        if (!id) {
            id = 'dev_' + Math.random().toString(36).substr(2,12) + Date.now().toString(36);
            localStorage.setItem('democrasify_device_id', id);
        }
        return id;
    })()""",
    key="device_id_fetch"
)

if not device_id:
    st.info("Yükleniyor...")
    st.stop()

device_votes = get_device_votes(device_id)

# ── Başlık & Engel Kontrolü ──────────────────────────────────────────────────

st.title("🎵 Yolculuk Playlisti")

if st.session_state.fail_count >= 3:
    st.error("Çok fazla hatalı giriş denemesi. Sisteme erişiminiz engellendi.")
    st.stop()

st.markdown(f"""
Listeyi oylayarak sıralamayı belirleyin, ya da yeni şarkı önerin! &nbsp;
<span id="countdown" style="color:#888;font-size:0.85em;">({REFRESH_SECS}s)</span>
<script>
(function(){{
    var secs = {REFRESH_SECS};
    var el = document.getElementById('countdown');
    if (!el) return;
    var iv = setInterval(function(){{
        secs--;
        if (secs <= 0) {{ secs = {REFRESH_SECS}; }}
        if (el) el.innerText = '(' + secs + 's)';
    }}, 1000);
}})();
</script>
""", unsafe_allow_html=True)
st.divider()

# ── Playlist ─────────────────────────────────────────────────────────────────

df = get_songs()

if df.empty:
    st.info("Henüz onaylanmış şarkı yok. Aşağıdan öneri gönderebilirsin!")
else:
    for _, row in df.iterrows():
        song_id = str(int(row['id']))

        col_info, col_like, col_dislike = st.columns([4, 1, 1], vertical_alignment="center")

        vote_val   = device_votes.get(song_id)
        is_voted   = vote_val is not None

        with col_info:
            artist_text = f" - {row['artist']}" if row['artist'] else ""
            st.markdown(f"**{row['title']}**{artist_text}")
            st.caption(f"Öneren: {row['added_by']} | Skor: **{int(row['score'])}**")

        with col_like:
            label    = "✅ 👍" if vote_val == "like" else "👍"
            btn_type = "primary" if vote_val == "like" else "secondary"
            if st.button(label, key=f"like_{song_id}", disabled=is_voted, type=btn_type):
                record_vote(device_id, int(row['id']), 'like')
                st.rerun()

        with col_dislike:
            label    = "✅ 👎" if vote_val == "dislike" else "👎"
            btn_type = "primary" if vote_val == "dislike" else "secondary"
            if st.button(label, key=f"dislike_{song_id}", disabled=is_voted, type=btn_type):
                record_vote(device_id, int(row['id']), 'dislike')
                st.rerun()

        st.write("---")

# ── Şarkı Öneri Formu ────────────────────────────────────────────────────────

st.divider()
st.subheader("➕ Şarkı Öner")
with st.form("add_song_form", clear_on_submit=True):
    new_title    = st.text_input("Şarkı Adı *", max_chars=100)
    new_artist   = st.text_input("Şarkıcı / Grup", max_chars=100)
    new_added_by = st.text_input("Öneren Kişi (Adınız) *", max_chars=50)

    submitted = st.form_submit_button("Gönder")
    if submitted:
        if not new_title.strip() or not new_added_by.strip():
            st.error("Lütfen 'Şarkı Adı' ve 'Öneren Kişi' alanlarını doldurun.")
        else:
            add_song(new_title.strip(), new_artist.strip(), new_added_by.strip())
            st.success(f"'{new_title}' önerildi! Admin onayından sonra listede görünecek.")

# ── Admin Paneli ─────────────────────────────────────────────────────────────

st.divider()
with st.expander("Admin Paneli"):
    if not st.session_state.admin_logged:
        admin_pw = st.text_input("Admin Şifresi", type="password", key="admin_pw")
        if st.button("Giriş Yap"):
            if admin_pw == st.secrets["ADMIN_PASSWORD"]:
                st.session_state.admin_logged = True
                st.session_state.fail_count   = 0
                st.rerun()
            else:
                st.session_state.fail_count += 1
                if st.session_state.fail_count >= 3:
                    st.rerun()
                else:
                    st.warning("Denemeyi bırak!")
    else:
        st.success("Admin olarak giriş yapıldı.")

        # ── Bekleyen Öneriler ──
        pending_df = get_pending_songs()
        if not pending_df.empty:
            st.subheader(f"⏳ Bekleyen Öneriler ({len(pending_df)})")
            for _, p_row in pending_df.iterrows():
                p_id = int(p_row['id'])
                artist_text = f" — {p_row['artist']}" if p_row['artist'] else ""
                col_t, col_a, col_r = st.columns([4, 1, 1])
                with col_t:
                    st.markdown(f"**{p_row['title']}**{artist_text}")
                    st.caption(f"Öneren: {p_row['added_by']}")
                with col_a:
                    if st.button("✅", key=f"approve_{p_id}", help="Onayla"):
                        approve_song(p_id)
                        st.rerun()
                with col_r:
                    if st.button("❌", key=f"reject_{p_id}", help="Reddet"):
                        reject_song(p_id)
                        st.rerun()
                st.write("---")
        else:
            st.info("Bekleyen öneri yok.")

        # ── Şarkı Silme ──
        if not df.empty:
            st.subheader("🗑️ Şarkı Sil")
            song_to_delete = st.selectbox(
                "Silinecek şarkıyı seçin:",
                df['id'].astype(int).astype(str) + " - " + df['title']
            )
            if st.button("Şarkıyı Sil"):
                del_id = int(song_to_delete.split(" - ")[0])
                delete_song(del_id)
                st.success("Şarkı silindi.")
                st.rerun()

        if st.button("Çıkış Yap"):
            st.session_state.admin_logged = False
            st.rerun()
