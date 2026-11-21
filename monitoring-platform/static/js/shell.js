// App shell: sidebar collapse, mobile drawer, and the top-bar title.
(function () {
  var app = document.getElementById('app');
  if (!app) return;
  var key = 'sidebarCollapsed';
  var toggle = app.querySelector('.sidebar-toggle');
  var menu = app.querySelector('.menu-btn');

  function setCollapsed(on) {
    app.classList.toggle('sidebar-collapsed', on);
    toggle.setAttribute('aria-expanded', String(!on));
    toggle.setAttribute('aria-label', on ? 'Expand sidebar' : 'Collapse sidebar');
    toggle.title = on ? 'Expand sidebar' : 'Collapse sidebar';
    try { localStorage.setItem(key, on ? '1' : '0'); } catch (e) { /* storage blocked: keep the default */ }
  }

  function setNav(open) {
    app.classList.toggle('nav-open', open);
    menu.setAttribute('aria-expanded', String(open));
  }

  // Every menu link loads a new page, which starts with the sidebar at the top. Keep its scroll position
  // across those page loads, and keep the current page's link in view when there is no saved position yet.
  var sidebar = app.querySelector('.sidebar');
  var scrollKey = 'sidebarScrollTop';
  var restored = false;
  try {
    var saved = sessionStorage.getItem(scrollKey);
    if (saved !== null) { sidebar.scrollTop = Number(saved); restored = true; }
  } catch (e) { /* storage blocked: start at the top */ }
  if (!restored) {
    var current = sidebar.querySelector('[aria-current="page"]');
    if (current) current.scrollIntoView({ block: 'nearest' });
  }
  window.addEventListener('pagehide', function () {
    try { sessionStorage.setItem(scrollKey, String(sidebar.scrollTop)); } catch (e) { /* ignore */ }
  });

  try { if (localStorage.getItem(key) === '1') setCollapsed(true); } catch (e) { /* ignore */ }
  toggle.addEventListener('click', function () {
    setCollapsed(!app.classList.contains('sidebar-collapsed'));
  });
  menu.addEventListener('click', function () {
    setNav(!app.classList.contains('nav-open'));
  });
  app.querySelector('[data-close-nav]').addEventListener('click', function () { setNav(false); });
  app.querySelectorAll('.sidebar a').forEach(function (link) {
    link.addEventListener('click', function () { setNav(false); });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') setNav(false);
  });
  var crumb = document.getElementById('crumb');
  if (crumb && !crumb.textContent.trim()) crumb.textContent = document.title;
})();
