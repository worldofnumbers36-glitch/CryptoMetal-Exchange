function toggleNav() {
  document.getElementById('navLinks').classList.toggle('open');
}

// Auto-dismiss alerts after 4 seconds
document.addEventListener('DOMContentLoaded', function() {
  const alerts = document.querySelectorAll('.alert');
  alerts.forEach(function(alert) {
    setTimeout(function() {
      alert.style.transition = 'opacity .5s';
      alert.style.opacity = '0';
      setTimeout(function() { alert.remove(); }, 500);
    }, 4000);
  });
});
