/* ── main.js — NST Phone Repair Chatbot ── */

// ============================================================
// DOM References
// ============================================================
const resultPanel = document.getElementById('result-panel');
const btnPrint    = document.getElementById('btn-print');
const printNama   = document.getElementById('print-nama');
const printTipe   = document.getElementById('print-tipe');
const printWaktu  = document.getElementById('print-waktu');
const printKeluhan= document.getElementById('print-keluhan');

window._lastResult = null;

// ============================================================
// Utilities
// ============================================================
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

  function getFeatureConfirmationLabel(name) {
    const labels = {
      Baterai: 'Baterai',
      Kamera: 'Kamera',
      LCD: 'Layar',
      Touchscreen: 'Layar sentuh',
      'Konektor Cas': 'Konektor pengisian',
      Speaker: 'Speaker',
      Mikrofon: 'Mikrofon',
      Signal: 'Sinyal',
      Wifi: 'WiFi',
      Tegangan: 'Daya listrik',
      Backdoor: 'Penutup belakang',
      Housing: 'Bodi iPhone',
      Tombol: 'Tombol',
    };
    return labels[name] || name;
  }

  function getDiagnosisSummary(data) {
    const topics = (data.clinical_knowledge || [])
      .map(item => item.damage)
      .filter(Boolean);
    const uniqueTopics = [...new Set(topics)];
    if (uniqueTopics.length > 1) return uniqueTopics.join(' dan ');
    return data.top_diagnosis || '-';
  }

// ============================================================
// Chatbot State Machine (Max 3 consultations per session)
// ============================================================
let chatState = {
  step: 0, // 0: ask name, 1: ask phone type, 2: ask complaint, 3: post-diagnosis waiting choice
  name: '',
  tipe_hp: '',
  keluhan: '',
  pending_features: null,
  count: 0,
  max_count: 3
};

function addMsg(text, from, isHtml) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = from === 'bot' ? 'chat-msg-bot' : 'chat-msg-user';
  if (isHtml) {
    div.innerHTML = text;
  } else {
    div.textContent = text;
  }
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

function showTyping() {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'chat-msg-bot';
  div.id = 'typing-bubble';
  div.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function removeTyping() {
  const el = document.getElementById('typing-bubble');
  if (el) el.remove();
}

function resetChatSession() {
  chatState = { step: 0, name: '', tipe_hp: '', keluhan: '', pending_features: null, count: 0, max_count: 3 };
  resultPanel.innerHTML = '<div class="result-placeholder"><svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg><span>Hasil diagnosis akan muncul di sini setelah percakapan selesai.</span></div>';
  if (btnPrint) btnPrint.style.display = 'none';
  addMsg('Sesi baru dimulai! 😊 Boleh tahu nama kamu?', 'bot');
  const input = document.getElementById('chat-input');
  if (input) {
    input.placeholder = 'Ketik nama kamu...';
    input.focus();
  }
}

function promptNextComplaint() {
  if (chatState.count >= chatState.max_count) {
    addMsg('⚠️ Batas konsultasi untuk sesi ini (3/3) telah tercapai. Silakan mulai sesi baru untuk mendiagnosa perangkat lain.', 'bot');
    const actionDiv = addMsg('', 'bot', true);
    actionDiv.innerHTML = `
      <div class="chat-action-buttons">
        <button class="chat-action-btn" onclick="resetChatSession()">🔄 Mulai Sesi Baru</button>
      </div>`;
    return;
  }

  chatState.step = 2; // Langsung minta keluhan berikutnya
  const nextNum = chatState.count + 1;
  addMsg(`Silakan ceritakan keluhan lainnya pada <b>${escHtml(chatState.tipe_hp)}</b> kamu (Konsultasi ke-${nextNum} dari 3):`, 'bot', true);
  const input = document.getElementById('chat-input');
  if (input) {
    input.placeholder = 'Ketik keluhan lainnya...';
    input.focus();
  }
}

function isValidIphoneType(text) {
  const clean = text.toLowerCase().trim();
  
  // Merk non-Apple
  const nonAppleBrands = ['samsung', 'xiaomi', 'oppo', 'vivo', 'realme', 'asus', 'rog', 'infinix', 'poco', 'huawei', 'nokia', 'android', 'advan', 'sony', 'lenovo', 'lg'];
  const containsNonApple = nonAppleBrands.some(brand => clean.includes(brand));
  if (containsNonApple) {
    return false;
  }

  // Kata kunci keluhan/gejala umum yang mengindikasikan input bukan tipe ponsel
  const symptomKeywords = [
    'kamera', 'layar', 'batre', 'baterai', 'pecah', 'mati', 'getar', 'cas', 'charger', 
    'wifi', 'sinyal', 'speaker', 'suara', 'mic', 'tombol', 'backdoor', 'housing', 
    'tidak', 'bisa', 'rusak', 'kalo', 'kalau', 'saya', 'hp', 'ponsel', 'kenapa',
    'blank', 'panas', 'cepat', 'habis', 'bunyi', 'rekam', 'sentuh', 'macet', 'heng',
    'error', 'lupa', 'pola', 'sandi', 'passcode', 'suka', 'sering', 'tiba', 'tengah'
  ];
  const containsSymptom = symptomKeywords.some(keyword => clean.includes(keyword));
  if (containsSymptom) {
    return false;
  }

  // Cek apakah input menyerupai tipe iPhone
  const isIphoneWord = clean.includes('iphone') || clean.includes('ip') || clean.includes('i phone');
  const hasNumbers = /\d+/.test(clean);
  const isSpecialModel = clean.includes('xs') || clean.includes('xr') || clean.includes('se') || clean === 'x' || clean.includes('pro') || clean.includes('max') || clean.includes('plus') || clean.includes('mini');

  return isIphoneWord || hasNumbers || isSpecialModel;
}

function handleChatSend() {
  const input = document.getElementById('chat-input');
  const text  = input.value.trim();
  if (!text) return;
  addMsg(text, 'user');
  input.value = '';

  if (chatState.step === 0) {
    chatState.name = text;
    chatState.step = 1;
    setTimeout(() => {
      addMsg('Halo, <b>' + escHtml(text) + '</b>! Boleh tahu tipe iPhone kamu? (contoh: iPhone 12, iPhone 13 Pro)', 'bot', true);
      input.placeholder = 'Ketik tipe iPhone...';
    }, 400);
  } else if (chatState.step === 1) {
    if (!isValidIphoneType(text)) {
      setTimeout(() => {
        addMsg('Maaf, format atau tipe iPhone tidak dikenali. Sistem ini hanya dirancang untuk mendiagnosa perangkat iPhone. Silakan masukkan tipe iPhone Anda dengan benar (contoh: <b>iPhone 11</b>, <b>iPhone XS</b>, <b>iPhone 13 Pro</b>):', 'bot', true);
        input.placeholder = 'Ketik tipe iPhone...';
      }, 400);
      return;
    }
    chatState.tipe_hp = text;
    chatState.step = 2;
    setTimeout(() => {
      addMsg('Oke, <b>' + escHtml(chatState.tipe_hp) + '</b>. Sekarang ceritakan keluhannya — apa yang terasa tidak normal dari ponsel kamu?', 'bot', true);
      input.placeholder = 'Ketik keluhan iPhone kamu...';
    }, 400);
  } else if (chatState.step === 2) {
    chatState.keluhan = text;
    chatState.step = 3;
    showTyping();
    setTimeout(() => runFeatureExtraction(), 600);
  } else {
    // Jika pengguna mengetik teks langsung setelah diagnosa selesai, anggap sebagai keluhan lanjutan (jika kuota masih ada)
    if (chatState.count < chatState.max_count) {
      chatState.keluhan = text;
      chatState.step = 3;
      showTyping();
      setTimeout(() => runFeatureExtraction(), 600);
    } else {
      addMsg('Batas 3x konsultasi untuk sesi ini telah selesai. Silakan klik tombol di bawah untuk memulai sesi baru.', 'bot');
      const actionDiv = addMsg('', 'bot', true);
      actionDiv.innerHTML = `
        <div class="chat-action-buttons">
          <button class="chat-action-btn" onclick="resetChatSession()">🔄 Mulai Sesi Baru</button>
        </div>`;
    }
  }
}

async function runFeatureExtraction() {
  const { tipe_hp, keluhan } = chatState;
  try {
    const res = await fetch('/extract-features', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tipe_hp, keluhan })
    });
    const data = await res.json();
    removeTyping();
    if (!data.success) {
      addMsg('Keluhan belum dapat dipahami: ' + (data.error || 'Server error.'), 'bot');
      chatState.step = 2;
      return;
    }
    if (data.software_detected) {
      showTyping();
      return runChatDiagnosis();
    }

    chatState.pending_features = data.features;
      const items = (data.abnormal_features || [])
        .map(item => `<li><b>${escHtml(getFeatureConfirmationLabel(item.name))}</b></li>`)
      .join('');
    const summary = items
      ? `<ul style="margin:8px 0 0 18px">${items}</ul>`
      : '<div style="margin-top:8px">Belum ada gejala hardware spesifik yang terpetakan.</div>';
    const provider = data.extraction && data.extraction.provider
      ? `Penafsir: ${escHtml(data.extraction.provider)}`
      : 'Penafsir tervalidasi';
    addMsg(`<b>Apakah komponen yang bermasalah ini benar?</b>${summary}<div style="font-size:11px; margin-top:8px; opacity:.7">${provider}</div>`, 'bot', true);
    const actionDiv = addMsg('', 'bot', true);
    actionDiv.id = 'feature-confirmation';
    actionDiv.innerHTML = `
      <div class="chat-action-buttons">
        <button class="chat-action-btn" onclick="confirmExtractedFeatures()">✓ Benar, lanjut diagnosis</button>
        <button class="chat-action-btn secondary" onclick="retryFeatureExtraction()">✎ Tidak sesuai, jelaskan ulang</button>
      </div>`;
  } catch (err) {
    removeTyping();
    addMsg('Koneksi ke penafsir keluhan gagal: ' + err.message, 'bot');
    chatState.step = 2;
  }
}

function confirmExtractedFeatures() {
  const confirmation = document.getElementById('feature-confirmation');
  if (confirmation) confirmation.remove();
  if (!chatState.pending_features) {
    chatState.step = 2;
    return;
  }
  showTyping();
  runChatDiagnosis(chatState.pending_features);
}

function retryFeatureExtraction() {
  const confirmation = document.getElementById('feature-confirmation');
  if (confirmation) confirmation.remove();
  chatState.pending_features = null;
  chatState.step = 2;
  addMsg('Baik, jelaskan ulang keluhannya dengan menyebut komponen dan kondisinya. Contoh: “layar sulit disentuh dan baterai cepat habis”.', 'bot');
  const input = document.getElementById('chat-input');
  if (input) input.focus();
}

async function runChatDiagnosis(confirmedFeatures = null) {
  const { name, tipe_hp, keluhan } = chatState;
  try {
    const res  = await fetch('/diagnosa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        nama: name,
        tipe_hp,
        keluhan,
        ...(confirmedFeatures ? { features: confirmedFeatures } : {})
      })
    });
    const data = await res.json();
    removeTyping();

    if (!data.success) {
      addMsg('Maaf, terjadi kesalahan: ' + (data.error || 'Server error.'), 'bot');
      chatState.step = 2; // retry keluhan
      return;
    }

    window._lastResult = data;
    chatState.pending_features = null;
    chatState.count++;

    // Fill print fields
    if (printNama)    printNama.textContent    = data.customer.nama;
    if (printTipe)    printTipe.textContent    = data.customer.tipe_hp;
    if (printWaktu)   printWaktu.textContent   = new Date().toLocaleString('id-ID', { dateStyle: 'long', timeStyle: 'short' });
    if (printKeluhan) printKeluhan.textContent = data.input.raw;
    if (btnPrint)     btnPrint.style.display   = 'inline-flex';

    renderResult(data);

    if (data.is_general) {
      addMsg('<b>Analisis AI:</b> ' + escHtml(data.ai_advice || 'Gejala yang Anda keluhkan tidak spesifik mengarah ke kerusakan komponen hardware tertentu.'), 'bot', true);
      addMsg('Rekomendasi langkah pemeriksaan mandiri telah ditampilkan di sebelah kanan.', 'bot');
    } else {
        const diagnosa = getDiagnosisSummary(data);
        addMsg('Analisis selesai! Berdasarkan inferensi pakar DS-PSO, perkiraan kerusakan: <b>' + escHtml(diagnosa) + '</b>.', 'bot', true);
    }

    // Tampilkan tombol aksi interaktif untuk konsultasi berikutnya
    const remaining = chatState.max_count - chatState.count;
    const actionDiv = addMsg('', 'bot', true);

    if (chatState.count < chatState.max_count) {
      actionDiv.innerHTML = `
        <div style="font-size:12px; color:var(--gray-600); margin-bottom:8px;">
          Sesi aktif: <b>${escHtml(chatState.name)}</b> (${escHtml(chatState.tipe_hp)}) · Sisa kuota: <b>${remaining}x lagi</b>
        </div>
        <div class="chat-action-buttons">
          <button class="chat-action-btn" onclick="promptNextComplaint()">➕ Tanya / Diagnosa Keluhan Lain</button>
          <button class="chat-action-btn secondary" onclick="resetChatSession()">🔄 Selesai & Mulai Sesi Baru</button>
        </div>`;
    } else {
      actionDiv.innerHTML = `
        <div style="font-size:12px; color:var(--gray-600); margin-bottom:8px;">
          ✅ Anda telah mencapai batas maksimal <b>3x konsultasi</b> untuk sesi ini.
        </div>
        <div class="chat-action-buttons">
          <button class="chat-action-btn" onclick="resetChatSession()">🔄 Mulai Sesi Baru</button>
        </div>`;
    }

  } catch (err) {
    removeTyping();
    addMsg('Koneksi ke server gagal: ' + err.message, 'bot');
    chatState.step = 2;
  }
}

// ============================================================
// Result Rendering
// ============================================================
function getCustomerExplanation(diagnosis) {
  const map = {
    'Baterai Rusak':        'Baterai iPhone kamu sudah menurun kinerjanya sehingga tidak bisa menyimpan daya dengan baik. Ini kerusakan umum — bisa diperbaiki dengan mengganti baterai baru.',
    'LCD Rusak':            'Layar iPhone mengalami kerusakan — bisa tampilan gelap, warna aneh, atau garis-garis. Perlu penggantian modul LCD/OLED.',
    'Touchscreen Rusak':    'Layar sentuh tidak merespons atau meleset. Biasanya perlu penggantian digitizer atau modul layar.',
    'IC Power Rusak':       'Komponen utama pengatur daya bermasalah — ponsel tidak mau menyala atau sering mati sendiri. Perlu teknisi berpengalaman.',
    'IC Cas Rusak':         'Chip pengisian daya bermasalah — baterai tidak terisi meski sudah disambungkan ke charger.',
    'Port Pengisian Rusak': 'Port charger kotor atau rusak sehingga tidak bisa mengisi daya. Kadang cukup dibersihkan, atau perlu penggantian port.',
    'Speaker Rusak':        'Speaker tidak mengeluarkan suara atau suaranya kecil/pecah. Perlu penggantian komponen speaker.',
    'Mikrofon Rusak':       'Mikrofon bermasalah — suara tidak terdengar saat menelepon atau merekam.',
    'IC Audio Rusak':       'Chip audio internal bermasalah — tidak ada suara sama sekali. Perlu perbaikan pada motherboard.',
    'Kamera Rusak':         'Kamera mengalami kerusakan — gambar buram, tidak bisa dibuka, atau ada bercak di foto.',
    'Tombol Rusak':         'Tombol fisik (volume, power, atau home) tidak berfungsi. Biasanya perlu penggantian tombol.',
    'Antena Signal Rusak':  'Antena jaringan bermasalah — sinyal lemah atau tidak ada. Perlu pemeriksaan dan perbaikan antena.',
    'Antena Wifi Rusak':    'Antena WiFi bermasalah — WiFi tidak terdeteksi atau sering putus.',
    'Housing Rusak':        'Casing/bodi luar retak atau penyok. Perlu penggantian housing.',
    'Backdoor Rusak':       'Penutup belakang retak atau rusak. Perlu penggantian backdoor/back glass.',
    'IC WTR Rusak':         'Chip transceiver (WTR) bermasalah — sinyal jaringan sangat lemah atau tidak ada.',
  };
  return map[diagnosis] || 'Kerusakan terdeteksi. Bawa ke toko kami untuk pemeriksaan lebih lanjut.';
}

function getDamageIcon(diagnosis) {
  const icons = {
    'Baterai Rusak':    '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="16" height="10" rx="2"/><path d="M22 11v2"/></svg>',
    'LCD Rusak':        '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
    'Touchscreen Rusak':'<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"/><line x1="6" y1="1" x2="6" y2="4"/><line x1="10" y1="1" x2="10" y2="4"/><line x1="14" y1="1" x2="14" y2="4"/></svg>',
    'Speaker Rusak':    '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>',
    'Kamera Rusak':     '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>',
  };
  return icons[diagnosis] || '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>';
}

function getExpertAdviceHtml(advice, diagnosis = '', percentage = null, position = null) {
  if (!advice) return '';
  
  const gejalaHtml = (advice.gejala_utama || []).map(g => `<li style="margin-bottom: 4px;">${escHtml(g)}</li>`).join('');
  const penyebabHtml = (advice.penyebab_umum || []).map(p => `<li style="margin-bottom: 4px;">${escHtml(p)}</li>`).join('');
  const diagnosisHtml = (advice.langkah_diagnosis || []).map(d => `<li style="margin-bottom: 4px;">${escHtml(d)}</li>`).join('');
  const prosedurHtml = (advice.prosedur_perbaikan || []).map(pr => `<li style="margin-bottom: 4px;">${escHtml(pr)}</li>`).join('');
  
  let rincianBiayaHtml = '';
  if (advice.estimasi_biaya && advice.estimasi_biaya.rincian) {
    rincianBiayaHtml = '<ul style="margin-left:14px; margin-top:4px; font-size:11px; color:var(--gray-600); list-style-type:circle;">';
    for (const [k, v] of Object.entries(advice.estimasi_biaya.rincian)) {
      rincianBiayaHtml += `<li style="margin-bottom: 2px;">${escHtml(k)}: <b>${escHtml(v)}</b></li>`;
    }
    rincianBiayaHtml += '</ul>';
  }

  return `
    <div class="customer-section expert-advice-box" style="margin-top: 16px; border-top: 1px dashed var(--gray-200); padding-top: 16px;">
      <div class="customer-section-title" style="color:var(--primary); font-weight:600; display:flex; align-items:center; gap:6px;">
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 19.5A2.5 2.5 0 0 0 6.5 22H20M4 19.5V3.5A2.5 2.5 0 0 1 6.5 1H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5z"/></svg>
        ${position ? `${position}. ` : ''}Basis Pengetahuan Klinis Pakar (Lokal JSON)${diagnosis ? ` — ${escHtml(diagnosis)}` : ''}
      </div>
      <div style="background:var(--gray-50); border:1px solid var(--gray-200); border-radius:8px; padding:12px; margin-top:8px;">
        <div style="font-size:11px; font-weight:bold; text-transform:uppercase; color:var(--gray-500); margin-bottom:6px; display:flex; justify-content:space-between;">
          <span>KODE: ${escHtml(advice.kode || '-')}</span>
          <span>${percentage !== null ? `BETP: ${Number(percentage).toFixed(1)}% · ` : ''}KATEGORI: ${escHtml(advice.kategori || '-')}</span>
        </div>
        <div style="font-size:13px; color:var(--gray-700); line-height:1.5; margin-bottom:12px;">
          ${escHtml(advice.deskripsi_umum || '')}
        </div>

        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin-bottom:12px;">
          <div>
            <span style="font-size:12px; font-weight:600; color:var(--gray-800); display:block; border-bottom:1px solid var(--gray-200); padding-bottom:4px; margin-bottom:4px;">Gejala Utama:</span>
            <ul style="margin-left:14px; font-size:11px; color:var(--gray-600); line-height:1.4;">
              ${gejalaHtml}
            </ul>
          </div>
          <div>
            <span style="font-size:12px; font-weight:600; color:var(--gray-800); display:block; border-bottom:1px solid var(--gray-200); padding-bottom:4px; margin-bottom:4px;">Penyebab Umum:</span>
            <ul style="margin-left:14px; font-size:11px; color:var(--gray-600); line-height:1.4;">
              ${penyebabHtml}
            </ul>
          </div>
        </div>

        <div style="margin-bottom:12px; border-top:1px solid var(--gray-200); padding-top:8px;">
          <span style="font-size:12px; font-weight:600; color:var(--gray-800); display:block; margin-bottom:6px;">Prosedur Diagnosis & Tindakan Teknisi:</span>
          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px;">
            <div>
              <span style="font-size:11px; font-weight:600; color:var(--gray-500); display:block; margin-bottom:4px;">Langkah Diagnosis:</span>
              <ol style="margin-left:14px; font-size:11px; color:var(--gray-600); line-height:1.4;">
                ${diagnosisHtml}
              </ol>
            </div>
            <div>
              <span style="font-size:11px; font-weight:600; color:var(--gray-500); display:block; margin-bottom:4px;">Prosedur Perbaikan:</span>
              <ul style="margin-left:14px; font-size:11px; color:var(--gray-600); line-height:1.4;">
                ${prosedurHtml}
              </ul>
            </div>
          </div>
        </div>

        <div style="border-top:1px solid var(--gray-200); padding-top:8px; display:grid; grid-template-columns:1.2fr 1fr; gap:12px; font-size:12px;">
          <div>
            <span style="color:var(--gray-800); font-weight:600;">Estimasi Biaya Servis:</span>
            <div style="font-weight:bold; color:#059669; font-size:13px; margin-top:2px;">
              ${escHtml(advice.estimasi_biaya?.rentang || '-')}
            </div>
            ${rincianBiayaHtml}
          </div>
          <div style="line-height:1.4; display:flex; flex-direction:column; justify-content:center;">
            <div><span style="color:var(--gray-500);">Tingkat Kesulitan:</span> <span style="font-weight:600; color:var(--gray-800);">${escHtml(advice.tingkat_kesulitan || '-')}</span></div>
            <div><span style="color:var(--gray-500);">Waktu Pengerjaan:</span> <span style="font-weight:600; color:var(--gray-800);">${escHtml(advice.waktu_perbaikan || '-')}</span></div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderResult(data) {
  if (data.is_software) {
    const adviceHtml = getExpertAdviceHtml(data.expert_advice);
    resultPanel.innerHTML = `
      <div class="fallback-notice" style="display:block; background:var(--primary-light); border-color:#BFDBFE; color:var(--primary-dark);">
        ℹ Masalah Sistem Operasi Terdeteksi (Software Gate Filter)
      </div>
      <div class="top-diagnosis-box" style="background:var(--success-light); border-color:#A7F3D0;">
        <div class="top-diag-icon" style="background:var(--success);">
          <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
          </svg>
        </div>
        <div>
          <div class="top-diag-label" style="color:var(--success);">Kesimpulan Diagnosis</div>
          <div class="top-diag-name" style="color:#065F46;">${escHtml(data.top_diagnosis)}</div>
          <div class="top-diag-pct" style="color:#065F46;">Klasifikasi kata kunci · Di luar evaluasi model DS-PSO</div>
        </div>
      </div>
      
      ${adviceHtml}
      
      <div style="font-size:13px; color:var(--gray-700); line-height:1.6; padding:14px; background:var(--gray-50); border:1px solid var(--gray-200); border-radius:8px; margin-top:12px;">
        <p style="margin-bottom:8px;"><b>Rekomendasi Tindakan:</b></p>
        <p style="margin-bottom:4px;">Untuk memastikan keamanan data dan lisensi sistem perangkat Anda, masalah perangkat lunak (software) tingkat lanjut ini sebaiknya ditangani secara resmi.</p>
        <p style="margin-bottom:0;">Silakan bawa iPhone Anda ke <b>Service Center Resmi Apple (AASP / iBox)</b> terdekat untuk proses pemulihan sistem yang aman.</p>
      </div>`;
    return;
  }

  if (data.is_general) {
    resultPanel.innerHTML = `
      <div class="customer-res-container">
        <div class="customer-res-header">
          <div class="customer-res-icon" style="background:#FEF3C7; color:#D97706;">
            <svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
            </svg>
          </div>
          <div class="customer-res-info">
            <div class="customer-res-label">Status Keluhan iPhone</div>
            <div class="customer-res-title" style="font-size:18px;">Gejala Umum / Perlu Pemeriksaan Fisik</div>
            <div class="customer-badge medium">🟡 Gejala Non-Spesifik Hardware</div>
          </div>
        </div>

        <div class="customer-section">
          <div class="customer-section-title">
            <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            Analisis Asisten AI
          </div>
          <div class="customer-section-p" style="background:#F0FDF4; border:1px solid #BBF7D0; padding:10px 12px; border-radius:8px; color:#166534; font-weight:500;">
            ${escHtml(data.ai_advice || 'Gejala tidak langsung mengarah ke kerusakan komponen hardware tertentu.')}
          </div>
        </div>

        <div class="customer-section">
          <div class="customer-section-title">
            <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
            Langkah Pengecekan Mandiri
          </div>
          <div style="font-size:13px; color:var(--gray-700); line-height:1.6; padding:10px 14px; background:var(--gray-50); border:1px solid var(--gray-200); border-radius:8px;">
            <ul style="margin-left:18px;">
              <li style="margin-bottom:4px;"><b>Restart iPhone:</b> Muat ulang ponsel untuk menyegarkan memori RAM dan menghentikan proses latar belakang yang macet.</li>
              <li style="margin-bottom:4px;"><b>Cek Kapasitas Memori:</b> Buka <i>Pengaturan > Umum > Penyimpanan iPhone</i> (pastikan ada sisa minimal 10GB).</li>
              <li style="margin-bottom:4px;"><b>Cek Kesehatan Baterai:</b> Buka <i>Pengaturan > Baterai > Kesehatan Baterai</i> (jika di bawah 80%, baterai dapat menyebabkan perlambatan performa).</li>
              <li style="margin-bottom:0;"><b>Hindari Suhu Ekstrem:</b> Jangan memainkan game berat saat ponsel sedang di-charge.</li>
            </ul>
          </div>
        </div>

        <div class="customer-section">
          <div class="customer-section-title">
            <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
            Saran Servis
          </div>
          <div class="customer-section-p">Jika ponsel tetap panas berlebih atau mati mendadak setelah langkah di atas, silakan bawa ke counter <b>NST Phone Repair</b> untuk pengecekan jalur arus motherboard dan konsumsi daya secara gratis.</div>
        </div>
      </div>`;
    return;
  }

  const topPct  = data.top_percentage;
  const allDiag = data.all_diagnoses || [];
  const topThreeDiag = allDiag.slice(0, 3);
  const maxPct  = allDiag[0].percentage;
  const confidence = data.confidence || {};
  const clinicalItems = Array.isArray(data.clinical_knowledge) && data.clinical_knowledge.length
    ? data.clinical_knowledge
    : [{ damage: data.top_diagnosis, percentage: data.top_percentage, advice: data.expert_advice }];
  const adviceHtml = clinicalItems
    .map((item, index) => getExpertAdviceHtml(item.advice, item.damage, item.percentage, clinicalItems.length > 1 ? index + 1 : null))
    .join('');
  const extractionProvider = data.extraction?.provider || 'unknown';
  const extractionFallback = data.extraction?.fallback ? ' (fallback)' : '';
  const topBel = Number(data.belief?.[data.top_diagnosis] || 0) * 100;
  const topPl = Number(data.plausibility?.[data.top_diagnosis] || 0) * 100;
  const thetaPct = Number(data.uncertainty_theta || 0) * 100;

  // Bar chart mini
  const barsHtml = topThreeDiag.map((d, i) => {
    const w = maxPct > 0 ? (d.percentage / maxPct * 100).toFixed(1) : 0;
    return `<div class="bar-row">
      <span class="bar-label" title="${escHtml(d.damage)}">${escHtml(d.damage)}</span>
      <div class="bar-track"><div class="bar-fill ${i === 0 ? 'bar-top' : 'bar-low'}" style="width:${w}%"></div></div>
      <span class="bar-pct">${d.percentage.toFixed(1)}%</span>
    </div>`;
  }).join('');

  resultPanel.innerHTML = `
    <div class="customer-res-container">
      <div class="customer-section">
        <div class="customer-section-title">
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
          Penjelasan Kerusakan
        </div>
        <div class="customer-section-p">${explanation}</div>
      </div>

      <div class="customer-section">
        <div class="customer-section-title">
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
          Distribusi Keyakinan DS-PSO
        </div>
        <div class="bar-list" style="margin-top:8px;">${barsHtml}</div>
        <div style="margin-top:10px; font-size:11px; color:var(--gray-500); line-height:1.5;">
          BetP: <b>${Number(topPct).toFixed(2)}%</b> · Bel: <b>${topBel.toFixed(2)}%</b> ·
          Pl: <b>${topPl.toFixed(2)}%</b> · Theta: <b>${thetaPct.toFixed(2)}%</b><br>
          Status evidence: <b>${escHtml(confidence.label || 'Indikasi awal')}</b> · Margin: <b>${(Number(confidence.top_two_margin || 0) * 100).toFixed(2)}%</b><br>
          Ekstraksi gejala: <b>${escHtml(extractionProvider + extractionFallback)}</b>
        </div>
      </div>

      ${adviceHtml}

      <div class="customer-section">
        <div class="customer-section-title">
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
          Saran Servis
        </div>
        <div class="customer-section-p">Bawa perangkat ke counter <b>NST Phone Repair</b>. Teknisi kami akan melakukan pengujian hardware gratis untuk mengonfirmasi sebelum pengerjaan.</div>
      </div>

      <div class="customer-actions print-hide">
        <button type="button" class="customer-btn-action primary" onclick="window.print()">
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>
          Cetak Nota Diagnosis
        </button>
      </div>
    </div>`;
}

// ============================================================
// Model Info (sidebar stats only)
// ============================================================
async function loadModelInfo() {
  try {
    const res  = await fetch('/model-info');
    const data = await res.json();
    const rulesEl   = document.getElementById('stat-rules');
    const classesEl = document.getElementById('stat-classes');
    if (rulesEl)   rulesEl.textContent   = data.total_rules;
    if (classesEl) classesEl.textContent = data.total_classes;
  } catch (err) {
    console.error('Gagal memuat info model:', err);
  }
}

// ============================================================
// Event Listeners
// ============================================================
document.getElementById('chat-send').addEventListener('click', handleChatSend);
document.getElementById('chat-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); handleChatSend(); }
});

// ============================================================
// Startup
// ============================================================
window.addEventListener('DOMContentLoaded', () => {
  loadModelInfo();
  // Tampilkan pesan sapaan awal
  setTimeout(() => {
    addMsg('Halo! Saya asisten NST Repair. Saya akan bantu diagnosa kerusakan iPhone kamu. 😊', 'bot');
    setTimeout(() => addMsg('Pertama, boleh tahu nama kamu?', 'bot'), 600);
  }, 300);
});
