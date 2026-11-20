// Screenshot gallery: image load states, the preview dialog, and request errors.
(function () {
  var dialog = document.getElementById('shot-preview');
  if (!dialog) return;

  var img = document.getElementById('shot-preview-img');
  var note = document.getElementById('shot-preview-note');
  var title = document.getElementById('shot-preview-title');
  var meta = document.getElementById('shot-preview-meta');
  var pos = document.getElementById('shot-preview-pos');
  var prev = document.getElementById('shot-prev');
  var next = document.getElementById('shot-next');
  var detail = document.getElementById('shot-detail');
  var download = document.getElementById('shot-download');
  var errorBox = document.getElementById('gallery-error');
  var current = 0;

  // The thumbnails on the page that is showing now, in display order.
  function thumbs() {
    return Array.prototype.slice.call(document.querySelectorAll('#gallery-wrap .shot-thumb[data-src]'));
  }

  function show(index) {
    var list = thumbs();
    if (!list.length) return;
    current = (index + list.length) % list.length;
    var data = list[current].dataset;

    title.textContent = data.name;
    meta.textContent = data.time + ' · ' + data.device + ' · ' + data.status;
    pos.textContent = (current + 1) + ' of ' + list.length + ' on this page';
    prev.disabled = list.length < 2;
    next.disabled = list.length < 2;
    detail.href = data.detail;
    if (download) {
      download.href = data.download || '#';
      download.hidden = !data.download;
    }

    note.hidden = false;
    note.textContent = 'Loading image…';
    img.hidden = true;
    img.alt = 'Screenshot of ' + data.name + ', ' + data.time;
    img.src = data.src;
  }

  img.addEventListener('load', function () {
    note.hidden = true;
    img.hidden = false;
  });
  img.addEventListener('error', function () {
    img.hidden = true;
    note.hidden = false;
    note.textContent = 'This image could not be loaded.';
  });

  document.addEventListener('click', function (event) {
    var thumb = event.target.closest('#gallery-wrap .shot-thumb[data-src]');
    if (thumb) {
      show(thumbs().indexOf(thumb));
      dialog.showModal();
      return;
    }
    if (event.target.closest('#shot-prev')) show(current - 1);
    if (event.target.closest('#shot-next')) show(current + 1);
    if (event.target.closest('[data-dialog-close]')) dialog.close();
    // A click on the backdrop lands on the dialog element itself.
    if (event.target === dialog) dialog.close();
  });

  document.addEventListener('keydown', function (event) {
    if (!dialog.open) return;
    if (event.key === 'ArrowLeft') show(current - 1);
    if (event.key === 'ArrowRight') show(current + 1);
  });

  // Thumbnails: fade in when loaded, show a message when the image cannot load.
  document.addEventListener('load', function (event) {
    var target = event.target;
    if (target.tagName === 'IMG' && target.closest('.shot-thumb')) target.classList.add('is-loaded');
  }, true);
  document.addEventListener('error', function (event) {
    var target = event.target;
    if (target.tagName === 'IMG' && target.closest('.shot-thumb')) {
      target.closest('.shot-thumb').classList.add('is-broken');
    }
  }, true);

  // Filter and page requests: show a message if the server cannot be reached.
  function showError(visible) {
    if (errorBox) errorBox.classList.toggle('is-visible', visible);
  }
  document.body.addEventListener('htmx:sendError', function () { showError(true); });
  document.body.addEventListener('htmx:responseError', function () { showError(true); });
  document.body.addEventListener('htmx:afterSwap', function () { showError(false); });
})();
