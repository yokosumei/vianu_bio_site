// Home hover -> actualizează panoul info
document.addEventListener('DOMContentLoaded', ()=>{
  const bridges = document.querySelectorAll('.bridge');
  const title = document.getElementById('info-title');
  const text  = document.getElementById('info-text');
  if (bridges && title && text) {
    bridges.forEach(b=>{
      b.addEventListener('mouseenter', ()=>{
        const info = b.dataset.info || 'Info';
        title.textContent = info;
        if(info.includes('CINE')) text.textContent = 'Despre club, valori, proiecte.';
        else if(info.includes('BLOG')) text.textContent = 'Articole, anunțuri, noutăți.';
        else if(info.includes('GALERIE')) text.textContent = 'Imagini din activități și evenimente.';
      });
    });
  }
});
