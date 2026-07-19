(function () {
  var MAX_LEN = 300;
  var box = document.getElementById('dm-app');
  var myId = box.dataset.myId;
  var otherId = box.dataset.otherId;
  var messages = document.getElementById('dm-messages');
  var input = document.getElementById('dm_input');
  var socket = io();

  socket.on('connect', function () {
    socket.emit('join_dm', { other_id: otherId });
  });

  socket.on('dm_message', function (data) {
    if (data.other_id !== otherId && data.sender_id !== otherId) return;
    appendMessage(data);
  });

  socket.on('chat_error', function (data) {
    var item = document.createElement('li');
    item.style.color = '#B71C1C';
    item.textContent = data.error;
    messages.appendChild(item);
  });

  function appendMessage(data) {
    var item = document.createElement('li');
    var who = data.sender_id === myId ? '나' : data.sender_name;
    item.textContent = who + ': ' + data.content;
    messages.appendChild(item);
    messages.scrollTop = messages.scrollHeight;
  }

  function sendMessage() {
    var message = input.value.trim();
    if (!message) return;
    if (message.length > MAX_LEN) {
      alert('메시지는 ' + MAX_LEN + '자 이하여야 합니다.');
      return;
    }
    socket.emit('dm_message', { other_id: otherId, message: message });
    input.value = '';
  }

  document.getElementById('dm_send').addEventListener('click', sendMessage);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') sendMessage();
  });
})();
