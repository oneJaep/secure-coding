(function () {
  var MAX_LEN = 300;
  var socket = io();
  var messages = document.getElementById('messages');
  var input = document.getElementById('chat_input');

  socket.on('connect_error', function (err) {
    messages.appendChild(errorItem('연결 실패: 로그인 상태를 확인해주세요.'));
  });

  socket.on('message', function (data) {
    var item = document.createElement('li');
    item.textContent = data.username + ': ' + data.message;
    messages.appendChild(item);
    messages.scrollTop = messages.scrollHeight;
  });

  socket.on('chat_error', function (data) {
    messages.appendChild(errorItem(data.error));
  });

  function errorItem(text) {
    var item = document.createElement('li');
    item.style.color = '#B71C1C';
    item.textContent = text;
    return item;
  }

  function sendMessage() {
    var message = input.value.trim();
    if (!message) return;
    if (message.length > MAX_LEN) {
      alert('메시지는 ' + MAX_LEN + '자 이하여야 합니다.');
      return;
    }
    socket.emit('send_message', { message: message });
    input.value = '';
  }

  document.getElementById('chat_send').addEventListener('click', sendMessage);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') sendMessage();
  });
})();
