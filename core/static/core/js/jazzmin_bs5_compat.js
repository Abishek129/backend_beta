/* Jazzmin 3.0.2 loads Bootstrap 5.3 JS but its templates still render
 * Bootstrap 4 markup (data-toggle="pill" / "dropdown"), which Bootstrap 5
 * ignores: change-form tabs never switch and the user menu never opens.
 * This bridges both. Drop it once Jazzmin ships markup matching its JS.
 */
(function () {
  'use strict';

  // Tabs: Bootstrap 5 drives them fine once they carry its own attribute.
  document.querySelectorAll('[data-toggle="pill"], [data-toggle="tab"]').forEach(function (el) {
    el.setAttribute('data-bs-toggle', el.getAttribute('data-toggle'));
  });

  // Dropdowns: Bootstrap 5 needs Popper, which Jazzmin does not load, so the
  // menu is toggled by hand.
  function closeAll(except) {
    document.querySelectorAll('.dropdown-menu.show').forEach(function (menu) {
      if (menu !== except) {
        menu.classList.remove('show');
        menu.parentElement.classList.remove('show');
      }
    });
  }

  document.addEventListener('click', function (event) {
    var toggle = event.target.closest('[data-toggle="dropdown"]');
    if (!toggle) {
      if (!event.target.closest('.dropdown-menu')) {
        closeAll(null);
      }
      return;
    }
    var menu = toggle.parentElement.querySelector('.dropdown-menu');
    if (!menu) {
      return;
    }
    event.preventDefault();
    var open = !menu.classList.contains('show');
    closeAll(menu);
    menu.classList.toggle('show', open);
    toggle.parentElement.classList.toggle('show', open);
    toggle.setAttribute('aria-expanded', String(open));
  });
})();
