from django.shortcuts import render, redirect
from django.http import JsonResponse, StreamingHttpResponse
import openai
import json
from django.contrib import auth
from django.contrib.auth.models import User
from .models import Chat, UserSetting, BotSetting
from django.utils import timezone

SESSION_KEY = 'chat_messages'

def ask_openai(message, request):
    user = request.user

    # 获取Bot设置
    try:
        bot_setting = BotSetting.objects.first()
        openai.api_key = bot_setting.apikey
        summary_cmd = bot_setting.summary_cmd
    except Exception:
        return "配置错误，请管理员在后台正确设置 Bot Setting"

    # 获取用户设置
    try:
        user_setting = UserSetting.objects.get(user=user)
    except UserSetting.DoesNotExist:
        user_setting = UserSetting.objects.create(user=user)

    prompt = user_setting.prompt

    # 从 session 读取当前用户的对话历史（每个用户独立，线程安全）
    messages = request.session.get(SESSION_KEY, [])

    if len(messages) == 0:
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user",   "content": message},
        ]
    elif len(messages) > user_setting.generate_summary_num * 2:
        summary = generate_summary(messages, summary_cmd, user_setting.modelName)
        messages = [
            {"role": "system",    "content": prompt},
            {"role": "user",      "content": summary_cmd},
            {"role": "assistant", "content": summary},
            {"role": "user",      "content": message},
        ]
    else:
        messages.append({"role": "user", "content": message})

    response = openai.chat.completions.create(
        model=user_setting.modelName,
        messages=messages
    )

    answer = response.choices[0].message.content.strip()
    messages.append({"role": "assistant", "content": answer})

    # 写回 session
    request.session[SESSION_KEY] = messages
    request.session.modified = True

    return answer

# Create your views here.
def chatbot(request):
    user=request.user
    if user.id is None:
        return redirect('login')
    else:
        chats = Chat.objects.filter(user=request.user)

    if request.method == 'POST':
        message = request.POST.get('message')
        response = ask_openai(message, request)

        chat = Chat(user=request.user, message=message, response=response, created_at=timezone.now())
        chat.save()
        return JsonResponse({'message': message, 'response': response})
    return render(request, 'chatbot.html', {'chats': chats})

def login(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = auth.authenticate(request, username=username, password=password)
        if user is not None:
            auth.login(request, user)
            return redirect('chatbot')
        else:
            error_message = '账号或密码错误'
            return render(request, 'login.html', {'error_message': error_message})
    else:
        return render(request, 'login.html')

def register(request):
    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password1 = request.POST['password1']
        password2 = request.POST['password2']

        if password1 == password2:
            try:
                user = User.objects.create_user(username, email, password1)
                user.save()
                UserSetting.objects.create(user=user)
                auth.login(request, user)
                return redirect('chatbot')
            except:
                error_message = '创建帐户出错'
                return render(request, 'register.html', {'error_message': error_message})
        else:
            error_message = '密码不匹配'
            return render(request, 'register.html', {'error_message': error_message})
    return render(request, 'register.html')

def logout(request):
    auth.logout(request)
    return redirect('login')

def generate_summary(messages, summary_cmd, model_name):
    # 不修改原列表，构造一份临时副本追加摘要指令
    send_chat = messages + [{"role": "user", "content": summary_cmd}]

    response = openai.chat.completions.create(
        model=model_name,
        messages=send_chat
    )

    return response.choices[0].message.content.strip()


def _build_messages(request, message):
    """构造发往 OpenAI 的消息列表，同时处理摘要逻辑。返回 (messages, user_setting, summary_cmd)"""
    user = request.user

    bot_setting = BotSetting.objects.first()
    openai.api_key = bot_setting.apikey
    summary_cmd = bot_setting.summary_cmd

    try:
        user_setting = UserSetting.objects.get(user=user)
    except UserSetting.DoesNotExist:
        user_setting = UserSetting.objects.create(user=user)

    prompt = user_setting.prompt
    messages = request.session.get(SESSION_KEY, [])

    if len(messages) == 0:
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user",   "content": message},
        ]
    elif len(messages) > user_setting.generate_summary_num * 2:
        summary = generate_summary(messages, summary_cmd, user_setting.modelName)
        messages = [
            {"role": "system",    "content": prompt},
            {"role": "user",      "content": summary_cmd},
            {"role": "assistant", "content": summary},
            {"role": "user",      "content": message},
        ]
    else:
        messages.append({"role": "user", "content": message})

    return messages, user_setting


def chatbot_stream(request):
    """流式接口：使用 SSE 逐块返回 OpenAI 回复"""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    message = request.POST.get('message', '').strip()
    if not message:
        return JsonResponse({'error': 'Empty message'}, status=400)

    user = request.user

    def event_stream():
        try:
            messages, user_setting = _build_messages(request, message)
        except Exception as e:
            yield f"data: {json.dumps({'error': '配置错误，请管理员在后台正确设置 Bot Setting'})}\n\n"
            return

        full_answer = ""
        try:
            stream = openai.chat.completions.create(
                model=user_setting.modelName,
                messages=messages,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    full_answer += delta
                    yield f"data: {json.dumps({'chunk': delta})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        # 流结束后保存到数据库和 session
        messages.append({"role": "assistant", "content": full_answer})
        request.session[SESSION_KEY] = messages
        request.session.modified = True

        Chat.objects.create(
            user=user,
            message=message,
            response=full_answer,
            created_at=timezone.now(),
        )

        yield f"data: {json.dumps({'done': True})}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'  # 禁用 Nginx 缓冲，确保实时推送
    return response
