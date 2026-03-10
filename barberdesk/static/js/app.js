// BarberDesk - App JavaScript

// Register Service Worker
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js')
            .then(reg => console.log('SW registered'))
            .catch(err => console.log('SW registration failed'));
    });
}

// PWA Install Prompt
let deferredPrompt;
window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;

    // Show install banner on barber pages
    if (window.location.pathname.startsWith('/barbeiro')) {
        const banner = document.createElement('div');
        banner.className = 'pwa-install-banner';
        banner.innerHTML = `
            <div class="d-flex justify-content-between align-items-center">
                <div>
                    <strong>Instalar BarberDesk</strong><br>
                    <small>Acesse rapido pela tela inicial</small>
                </div>
                <div>
                    <button class="btn btn-light btn-sm me-2" id="pwa-install">Instalar</button>
                    <button class="btn btn-outline-light btn-sm" id="pwa-dismiss">X</button>
                </div>
            </div>
        `;
        document.body.appendChild(banner);

        document.getElementById('pwa-install').addEventListener('click', () => {
            deferredPrompt.prompt();
            deferredPrompt.userChoice.then(() => { banner.remove(); });
        });

        document.getElementById('pwa-dismiss').addEventListener('click', () => {
            banner.remove();
        });
    }
});

// Utility: Format currency
function formatCurrency(value) {
    return 'R$ ' + parseFloat(value).toFixed(2).replace('.', ',');
}

// Utility: Format phone
function formatPhone(phone) {
    const cleaned = phone.replace(/\D/g, '');
    if (cleaned.length === 11) {
        return `(${cleaned.slice(0,2)}) ${cleaned.slice(2,7)}-${cleaned.slice(7)}`;
    }
    return phone;
}

// Auto-format phone inputs
document.querySelectorAll('input[type="tel"]').forEach(input => {
    input.addEventListener('input', function() {
        let value = this.value.replace(/\D/g, '');
        if (value.length > 11) value = value.slice(0, 11);
        if (value.length > 6) {
            this.value = `(${value.slice(0,2)}) ${value.slice(2,7)}-${value.slice(7)}`;
        } else if (value.length > 2) {
            this.value = `(${value.slice(0,2)}) ${value.slice(2)}`;
        }
    });
});
