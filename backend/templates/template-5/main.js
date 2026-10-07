/**
 * Intelligence Designed To Evolve
 * Vanilla JavaScript Implementation
 */

document.addEventListener('DOMContentLoaded', () => {
  initBackgroundVideo();
  initStatsCountUp();
  initMobileMenu();
  initNavLinks();
  initHashLinks();
});

/**
 * Ensure background video plays smoothly
 */
function initBackgroundVideo() {
  const video = document.querySelector('.bg-video');
  if (!video) return;

  video.muted = true;
  const playPromise = video.play();
  if (playPromise !== undefined) {
    playPromise.catch(() => {
      // Fallback: retry on user interaction
      const playOnTouch = () => {
        video.play().catch(() => {});
        window.removeEventListener('pointerdown', playOnTouch);
      };
      window.addEventListener('pointerdown', playOnTouch);
    });
  }
}

/**
 * Animated Count-up for Stats with easeOutCubic
 */
function initStatsCountUp() {
  const statItems = document.querySelectorAll('.stat-item');
  if (!statItems.length) return;

  const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

  let hasAnimated = false;

  const startAnimation = () => {
    if (hasAnimated) return;
    hasAnimated = true;

    statItems.forEach((item, index) => {
      const target = parseFloat(item.dataset.target || '0');
      const decimals = parseInt(item.dataset.decimals || '0', 10);
      const numEl = item.querySelector('.stat-num');
      if (!numEl) return;

      const duration = 1500 + index * 80;
      const startOffset = 480 + index * 90;

      setTimeout(() => {
        let startTime = null;

        const animate = (timestamp) => {
          if (!startTime) startTime = timestamp;
          const elapsed = timestamp - startTime;
          const progress = Math.min(elapsed / duration, 1);
          const currentVal = easeOutCubic(progress) * target;

          numEl.textContent = currentVal.toFixed(decimals);

          if (progress < 1) {
            requestAnimationFrame(animate);
          } else {
            numEl.textContent = target.toFixed(decimals);
          }
        };

        requestAnimationFrame(animate);
      }, startOffset);
    });
  };

  // IntersectionObserver threshold 0.25
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            startAnimation();
            observer.disconnect();
          }
        });
      },
      { threshold: 0.25 }
    );

    const statsFooter = document.querySelector('.stats-footer');
    if (statsFooter) {
      observer.observe(statsFooter);
    } else {
      startAnimation();
    }
  } else {
    startAnimation();
  }
}

/**
 * Mobile Navigation Drawer & Hamburger Toggle
 */
function initMobileMenu() {
  const burgerBtn = document.querySelector('.burger-btn');
  const mobileMenu = document.getElementById('mobile-menu');
  const mobileOverlay = document.getElementById('mobile-overlay');

  if (!burgerBtn || !mobileMenu || !mobileOverlay) return;

  function openMenu() {
    burgerBtn.setAttribute('aria-expanded', 'true');
    mobileMenu.hidden = false;
    mobileOverlay.hidden = false;
    mobileMenu.setAttribute('aria-hidden', 'false');
    document.body.classList.add('menu-open');
  }

  function closeMenu() {
    burgerBtn.setAttribute('aria-expanded', 'false');
    mobileMenu.hidden = true;
    mobileOverlay.hidden = true;
    mobileMenu.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('menu-open');
  }

  function toggleMenu() {
    const isOpen = burgerBtn.getAttribute('aria-expanded') === 'true';
    if (isOpen) {
      closeMenu();
    } else {
      openMenu();
    }
  }

  burgerBtn.addEventListener('click', toggleMenu);
  mobileOverlay.addEventListener('click', closeMenu);

  // Close on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.classList.contains('menu-open')) {
      closeMenu();
    }
  });

  // Close on mobile menu link click
  const mobileLinks = mobileMenu.querySelectorAll('a');
  mobileLinks.forEach((link) => {
    link.addEventListener('click', () => {
      closeMenu();
    });
  });

  // Close on window resize > 720px
  window.addEventListener('resize', () => {
    if (window.innerWidth > 720 && document.body.classList.contains('menu-open')) {
      closeMenu();
    }
  });
}

/**
 * Nav links active indicator synchronization
 */
function initNavLinks() {
  const desktopLinks = document.querySelectorAll('.desktop-nav .nav-link');
  const mobileLinks = document.querySelectorAll('.mobile-nav .mobile-nav-link');

  function setActive(href) {
    desktopLinks.forEach((link) => {
      if (link.getAttribute('href') === href) {
        link.classList.add('active');
      } else {
        link.classList.remove('active');
      }
    });

    mobileLinks.forEach((link) => {
      if (link.getAttribute('href') === href) {
        link.classList.add('active');
      } else {
        link.classList.remove('active');
      }
    });
  }

  desktopLinks.forEach((link) => {
    link.addEventListener('click', (e) => {
      const href = link.getAttribute('href');
      if (href && href.startsWith('#')) {
        e.preventDefault();
        setActive(href);
      }
    });
  });

  mobileLinks.forEach((link) => {
    link.addEventListener('click', (e) => {
      const href = link.getAttribute('href');
      if (href && href.startsWith('#')) {
        e.preventDefault();
        setActive(href);
      }
    });
  });
}

/**
 * Section copy for hash links that have no scroll target on this
 * single-screen layout. Every dead hash opens the overlay panel instead.
 */
/**
 * Dead hash links open a full-screen in-site subpage with real content.
 * #home returns to the home view.
 */
function initHashLinks() {
  const PAGE_KEYS = ['product', 'case-studies', 'contact', 'get-started', 'signin'];
  let lastFocus = null;

  function closeSubpage(silent) {
    const open = document.querySelectorAll('.subpage.is-open');
    if (!open.length) return;
    open.forEach((el) => {
      el.classList.remove('is-open');
      el.setAttribute('aria-hidden', 'true');
      el.scrollTop = 0;
    });
    if (!silent && lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();
    lastFocus = null;
  }

  function openSubpage(key) {
    const target = document.getElementById('page-' + key);
    if (!target) return;
    closeSubpage(true);
    lastFocus = document.activeElement;
    target.classList.add('is-open');
    target.setAttribute('aria-hidden', 'false');
    const back = target.querySelector('.subpage-back');
    if (back) back.focus();
  }

  document.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest) return;

    if (t.closest('[data-sp-close]') || t.closest('[data-sp-home]')) {
      e.preventDefault();
      closeSubpage();
      return;
    }

    const a = t.closest('a[href^="#"]');
    if (!a) return;
    const key = (a.getAttribute('href') || '').slice(1);
    if (key === 'home') {
      closeSubpage();
      return;
    }
    if (PAGE_KEYS.indexOf(key) === -1) return;
    if (!document.getElementById('page-' + key)) return;
    e.preventDefault();
    openSubpage(key);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSubpage();
  });
}
