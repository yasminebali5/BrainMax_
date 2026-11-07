(() => {
  const root = document.getElementById('brainmax-workspace');
  if (!root) return;
  const page = root.dataset.page;
  const csrf = document.querySelector('input[name=csrfmiddlewaretoken]')?.value || '';
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const readableCoachMessage = value => {
    const text=String(value??'').trim();
    const candidate=text.replace(/^```(?:json)?\s*|\s*```$/gi,'');
    try{
      const parsed=JSON.parse(candidate);
      if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed)
        &&'action' in parsed&&('ui' in parsed||'report_to_layer_a' in parsed)
        &&typeof parsed.message==='string'&&parsed.message.trim()){
        return parsed.message.trim();
      }
    }catch(error){
      if(!(error instanceof SyntaxError))throw error;
    }
    return text;
  };
  const subjects = {ml:'Machine learning',cyber:'Cybersecurity',ai:'Artificial intelligence'};
  let state = {profile:{},preferences:{},history:[]}, draft = {}, choice = null, confidence = null, busy = false;
  let questionTelemetry=null, lastQuestionActivity=0;
  let pathClock = null;
  const chatStorageKey=`brainmax-chat-${root.dataset.user}`;
  let chatHistory=[];
  try{
    const savedChatHistory=JSON.parse(sessionStorage.getItem(chatStorageKey)||'[]');
    if(Array.isArray(savedChatHistory)){
      chatHistory=savedChatHistory.filter(item=>
        item&&['user','assistant'].includes(item.role)&&typeof item.content==='string',
      ).map(item=>item.role==='assistant'
        ?{...item,content:readableCoachMessage(item.content)}
        :item);
    }
  }catch{
    sessionStorage.removeItem(chatStorageKey);
  }
  const bolt = document.getElementById('bolt'), sprite = document.getElementById('boltSprite'), pinkySprite = document.getElementById('pinkySprite');
  let voiceOn = false, lastSpeech = '', lastSpeaker = 'brain', selectedMood = '';
  let speakerPoseTimer = null;
  const recordQuestionActivity=()=>{
    if(questionTelemetry)lastQuestionActivity=performance.now();
  };
  document.addEventListener('pointermove',recordQuestionActivity,{passive:true});
  document.addEventListener('keydown',recordQuestionActivity);
  document.addEventListener('scroll',()=>{
    if(questionTelemetry)questionTelemetry.stimulus_seen=true;
  },{passive:true});
  document.addEventListener('visibilitychange',()=>{
    if(questionTelemetry&&document.hidden)questionTelemetry.tab_blurred=true;
  });

  async function api(endpoint, data) {
    const response = await fetch('/api/' + endpoint + '/', data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify(data)});
    let result;
    try { result = await response.json(); }
    catch { throw new Error(response.ok ? 'The server returned an unreadable response.' : `Request failed (${response.status}). Please try again.`); }
    if (!response.ok) { const failure=new Error(result.error || 'Please try again.');failure.status=response.status;throw failure; }
    return result;
  }
  function error(message) { const el = document.getElementById('bm-error'); if (el) el.textContent = message; speak('Something needs attention. ' + message, 'confused', '⚠️ Needs attention'); }
  async function task(fn) { if (busy) return; busy = true; try { await fn(); } catch (e) { error(e.message); } finally { busy = false; } }
  async function runCoachAction(action, button) {
    const destinations = {
      assessment: '/assessment/',
      learning_path: '/learning-path/',
      profile: '/profile/',
      account_profile: '/accounts/profile/',
      settings: '/settings/',
      brain_twin: '/brain-twin/',
      coach: '/ai-coach/',
      overview: '/dashboard/',
    };
    if (action.type === 'navigate_page') {
      const destination = destinations[action.destination];
      if (destination) {
        const startAssessment = (
          action.destination === 'assessment' && action.start_assessment
        );
        window.location.assign(
          startAssessment ? `${destination}?start=1` : destination
        );
      }
      return;
    }
    if (action.type === 'open_assessment') {
      window.location.href = '/assessment/';
      return;
    }
    if (action.type === 'dismiss_learning_plan') {
      button?.parentElement?.replaceChildren();
      return;
    }
    if (action.type === 'dismiss_site_action') {
      button?.parentElement?.replaceChildren();
      return;
    }
    if (action.type === 'download_data') {
      window.location.assign('/api/export/');
      return;
    }
    if (action.type === 'apply_site_action') {
      if (button) button.disabled = true;
      try {
        const result = await api('coach/site-action', {
          confirmation: action.confirmation,
        });
        const actions = button?.parentElement;
        if (actions) {
          actions.replaceChildren();
          const confirmation = document.createElement('p');
          confirmation.textContent = result.message;
          actions.append(confirmation);
        }
        const destinations = {
          dashboard: '/dashboard/',
          assessment: '/assessment/',
        };
        if (result.destination) {
          window.location.assign(destinations[result.destination]);
        } else if (action.confirmation) {
          window.setTimeout(() => window.location.reload(), 350);
        }
      } catch (e) {
        if (button) button.disabled = false;
        const errorMessage = document.getElementById('coachError');
        if (errorMessage) errorMessage.textContent = e.message;
        else document.getElementById('boltMessage').textContent = e.message;
      }
      return;
    }
    if (action.type === 'apply_learning_plan') {
      const originalLabel = button?.textContent;
      const actions = button?.parentElement;
      let actionStatus = actions?.querySelector('[data-action-status]');
      if (!actionStatus && actions) {
        actionStatus = document.createElement('p');
        actionStatus.dataset.actionStatus = '';
        actionStatus.setAttribute('role', 'status');
        actionStatus.setAttribute('aria-live', 'polite');
        actions.append(actionStatus);
      }
      const setStatus = (message, isError = false) => {
        if (!actionStatus) return;
        actionStatus.textContent = message;
        actionStatus.setAttribute('role', isError ? 'alert' : 'status');
      };
      if (button) {
        button.disabled = true;
        button.textContent = 'Applying plan…';
      }
      try {
        const result = await api('coach/learning-plan', {
          proposal: action.proposal,
        });
        if (action.after_apply === 'assessment') {
          if (button) button.textContent = 'Starting assessment…';
          setStatus('Plan confirmed. Preparing your assessment…');
          await api('assessment', {replace_active: true});
        }
        if (actions) {
          actions.replaceChildren();
          const confirmation = document.createElement('p');
          confirmation.setAttribute('role', 'status');
          confirmation.textContent = result.message;
          actions.append(confirmation);
        }
        const nextPage = destinations[action.after_apply];
        if (nextPage) {
          window.location.assign(
            action.after_apply === 'assessment'
              ? `${nextPage}?start=1`
              : nextPage
          );
        }
      } catch (e) {
        if (button) {
          button.disabled = false;
          button.textContent = originalLabel;
        }
        setStatus(e.message, true);
      }
      return;
    }
    if (action.type !== 'verification_retest') return;
    if (button) button.disabled = true;
    try {
      await api('pushback/retest', {assessment_id: action.assessment_id});
      window.location.href = '/assessment/';
    } catch (e) {
      if (button) button.disabled = false;
      const errorMessage = document.getElementById('coachError');
      if (errorMessage) errorMessage.textContent = e.message;
      else document.getElementById('boltMessage').textContent = e.message;
    }
  }
  function addCoachActions(container, actions) {
    if (!actions?.length) return;
    const group=document.createElement('div');
    group.className='coach-actions';
    (actions || []).forEach(action => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'bm-btn secondary';
      button.textContent = action.label;
      button.onclick = () => runCoachAction(action, button);
      group.append(button);
    });
    container.append(group);
  }
  function speak(message, pose='neutral', status='Listening', quick=[], speaker='brain') {
    if (!message) return;
    const messageEl = document.getElementById('boltMessage');
    const badgeEl = document.getElementById('boltBadge');
    const speakerEl = document.getElementById('boltSpeaker');
    const quickEl = document.getElementById('boltQuickReplies');
    if (messageEl) messageEl.textContent = message;
    if (badgeEl) badgeEl.textContent = status;
    if (!bolt) return;
    lastSpeaker = speaker === 'pinky' ? 'pinky' : 'brain';
    bolt.dataset.speaker = lastSpeaker;
    if (speakerEl) speakerEl.textContent = lastSpeaker === 'pinky' ? 'PINKY' : 'BRAIN';
    const safePose = bolt.dataset.mode==='assessment' ? 'quiet' : pose;
    if (sprite) sprite.dataset.pose = lastSpeaker === 'brain' ? safePose : bolt.dataset.mode==='assessment' ? 'quiet' : 'handoff';
    if (pinkySprite) pinkySprite.dataset.pose = lastSpeaker === 'pinky' ? safePose : bolt.dataset.mode==='assessment' ? 'quiet' : 'listening';
    if (quickEl) {
      quickEl.replaceChildren(...quick.map(label => { const b=document.createElement('button'); b.type='button'; b.textContent=label; b.onclick=()=>chat(label); return b; }));
    }
    lastSpeech = message;
    if (pose === 'speaking' && (!voiceOn || !('speechSynthesis' in window))) {
      speakerPoseTimer=window.setTimeout(()=>{
        if(lastSpeaker==='pinky'&&pinkySprite&&pinkySprite.dataset.pose==='speaking'){
          pinkySprite.dataset.pose='caring';
        }
      },2400);
    }
    if (voiceOn && 'speechSynthesis' in window) {
      speechSynthesis.cancel();
      const speakingAs = lastSpeaker;
      const u = new SpeechSynthesisUtterance(message);
      u.rate = bolt.dataset.mode === 'assessment' ? .96 : speakingAs === 'pinky' ? 1.04 : .94;
      u.pitch = bolt.dataset.mode === 'assessment' ? 1 : speakingAs === 'pinky' ? 1.18 : .88;
      const speechLanguage={en:'en',fr:'fr',ar:'ar'}[state.preferences?.language||'en'];
      if (speechLanguage) {
        u.lang={en:'en-US',fr:'fr-FR',ar:'ar-SA'}[speechLanguage];
        const voices=speechSynthesis.getVoices().filter(v=>v.lang?.toLowerCase().startsWith(speechLanguage));
        const preferred=speakingAs==='pinky'?/female|samantha|zira|victoria|jenny|aria/i:/male|daniel|david|mark|guy/i;
        u.voice=voices.find(v=>preferred.test(v.name)) || voices.find(v=>v.default) || voices[0] || null;
        if (bolt.dataset.mode !== 'assessment') {
          if (speakingAs === 'brain' && sprite) sprite.dataset.pose = 'speaking';
          else if (pinkySprite) pinkySprite.dataset.pose = 'speaking';
        }
        u.onend = () => {
          if (sprite) sprite.dataset.pose = speakingAs === 'brain' ? (pose==='speaking'?'neutral':safePose) : 'quiet';
          if (pinkySprite) pinkySprite.dataset.pose = speakingAs === 'pinky' ? (pose==='speaking'?'caring':safePose) : 'listening';
        };
        speechSynthesis.speak(u);
      }
    }
  }
  function mode(value) {
    if (bolt) bolt.dataset.mode=value;
    const boltModeEl = document.getElementById('boltMode');
    if (boltModeEl) boltModeEl.textContent=value==='assessment'?'Quiet assessment mode':value==='debrief'?'Results review':'Learning mode';
    if(value==='assessment' && sprite) { sprite.dataset.pose='quiet'; }
    if(value==='assessment' && pinkySprite) { pinkySprite.dataset.pose='quiet'; }
  }
  function chatContext(){
    let remaining=12000;
    return chatHistory.slice(0,-1).slice(-12).reverse().reduce((context,item)=>{
      if(remaining<=0||!item||!['user','assistant'].includes(item.role)||typeof item.content!=='string')return context;
      const limit=item.role==='user'?500:4000;
      const content=item.content.trim().slice(-Math.min(limit,remaining));
      if(!content)return context;
      remaining-=content.length;
      context.push({role:item.role,content});
      return context;
    },[]).reverse();
  }
  async function chat(message) {
    const text=String(message||'').trim();
    if(!text)return;
    const inAssessment = page==='assessment' && Boolean(state.assessment && !state.assessment.completed);
    const requested = inAssessment ? 'assessment' : state.latest_completed ? 'debrief' : 'learning';
    const transcript=document.getElementById('boltTranscript');
    const sendButton=document.getElementById('boltSend');
    const input=document.getElementById('boltInput');
    const addLine=(speaker,content,className,role=null,actions=[])=>{
      if(!transcript)return null;
      transcript.querySelector('.bolt-chat-empty')?.remove();
      const line=document.createElement('article');line.className=`bolt-transcript-line ${className}`;
      const label=document.createElement('strong');label.textContent=speaker;
      const body=document.createElement('span');body.textContent=content;
      line.append(label,body);
      addCoachActions(line,actions);
      transcript.append(line);transcript.scrollTop=transcript.scrollHeight;return line;
    };
    chatHistory.push({role:'user',content:text});
    sessionStorage.setItem(chatStorageKey,JSON.stringify(chatHistory));
    addLine(window.BrainMaxI18n.translate('You'),text,'user');
    const pending=addLine(/pinky/i.test(text)?'Pinky':'Brain',window.BrainMaxI18n.translate('Thinking…'),'pending');
    sendButton.disabled=true;input.disabled=true;
    speak(inAssessment?'One moment. Your answers stay private until submission.':'Thinking about your next step…',inAssessment?'quiet':'thinking',inAssessment?'🤫 Quiet mode':'🧠 Thinking');
    try {
      const result = await api('coach',{
        mode:requested,
        message:text,
        mood:selectedMood,
        language:page==='coach'?document.getElementById('coachLanguage')?.value:undefined,
        conversation:chatContext(),
      });
      if(pending)pending.remove();
      mode(result.mode);
      const speaker=result.speaker==='pinky'?'pinky':'brain';
      speak(result.message,result.mode==='assessment'?'quiet':result.growth?.trend==='up'?'growth-three':'neutral',result.status,result.quick_replies,speaker);
      chatHistory.push({role:'assistant',content:result.message});
      sessionStorage.setItem(chatStorageKey,JSON.stringify(chatHistory));
      addLine(speaker==='pinky'?'Pinky':'Brain',result.message,'assistant',null,result.actions);
      document.getElementById('boltMode').textContent += result.source ? ' · '+result.source : '';
    } catch(e) { if(pending)pending.remove();addLine('Brain',e.message,'error');speak(e.message,'confused','Needs attention'); }
    finally { sendButton.disabled=false;input.disabled=false; }
  }
  function renderChatHistory(){
    const transcript=document.getElementById('boltTranscript');
    if(!transcript) return;
    transcript.replaceChildren();
    if(!chatHistory.length){
      const welcome=document.createElement('p');
      welcome.className='bolt-chat-empty';
      welcome.textContent=window.BrainMaxI18n ? window.BrainMaxI18n.translate('I’m here to help you take the next step.') : 'I’m here to help you take the next step.';
      transcript.append(welcome);
      return;
    }
    chatHistory.forEach(item=>{
      const speaker=item.role==='user'?(window.BrainMaxI18n ? window.BrainMaxI18n.translate('You') : 'You'):'Brain';
      const line=document.createElement('article');
      line.className=`bolt-transcript-line ${item.role}`;
      const label=document.createElement('strong');
      label.textContent=speaker;
      const body=document.createElement('span');
      body.textContent=item.content;
      line.append(label,body);
      transcript.append(line);
    });
    transcript.scrollTop=transcript.scrollHeight;
  }
  renderChatHistory();
  document.getElementById('boltClear').onclick=()=>{
    chatHistory=[];
    sessionStorage.removeItem(chatStorageKey);
    renderChatHistory();
    document.getElementById('boltInput').focus();
  };
  document.getElementById('boltExpand').onclick=()=>{const box=document.getElementById('boltChat');box.hidden=!box.hidden;bolt.classList.remove('minimized');if(!box.hidden)document.getElementById('boltInput').focus();};
  document.getElementById('boltCollapse').onclick=()=>{bolt.classList.toggle('minimized'); if(bolt.classList.contains('minimized'))document.getElementById('boltChat').hidden=true;};
  document.getElementById('boltChatForm').onsubmit=event=>{event.preventDefault();const input=document.getElementById('boltInput');const message=input.value.trim();if(message){input.value='';chat(message);}};
  document.getElementById('brainAvatar').onclick=()=>chat('Brain, what is my next step?');
  document.getElementById('pinkyAvatar').onclick=()=>chat('Pinky, can you help me take a gentle pause?');
  document.getElementById('boltMood').onchange=e=>{
    const value=e.target.value;
    if(!value){selectedMood='';return;}
    selectedMood=value;
    const lines={confused:'We can take this one step at a time.',stressed:'Thanks for telling me. Take a comfortable pause, then continue when you feel ready.',frustrated:'A short break is okay. We can return when you are ready.',tired:'It is okay to pause; your progress will be here.',disengaged:'Let us choose one small, manageable step.',confident:'Wonderful momentum. Keep it going at your own pace.'};
    const line=bolt.dataset.mode==='assessment'?'No rush. Take a comfortable moment, then continue when ready.':lines[value];
    speak(line,bolt.dataset.mode==='assessment'?'quiet':value,'Here for you',[], 'pinky');
  };
  document.getElementById('boltVoice').onclick=()=>{
    voiceOn=!voiceOn;
    const button=document.getElementById('boltVoice');
    button.textContent=voiceOn?'🔊':'🔇';
    button.setAttribute('aria-label',voiceOn?'Turn voice off':'Turn voice on');
    if(!voiceOn && 'speechSynthesis' in window)speechSynthesis.cancel();
    else if(lastSpeech)speak(lastSpeech,sprite.dataset.pose,document.getElementById('boltBadge').textContent,[],lastSpeaker);
  };

  const head=(eyebrow,title,sub,action='')=>`<div class="bm-head"><div><span class="bm-eyebrow">${eyebrow}</span><h1>${title}</h1><p>${sub}</p></div>${action}</div>`;
  const button=(url,text,secondary=false)=>`<a class="bm-btn ${secondary?'secondary':''}" href="${url}">${text} →</a>`;
  const pathIcon=name=>`<svg class="bm-path-icon" aria-hidden="true" focusable="false"><use href="/static/assets/icons.svg#${name}"></use></svg>`;
  const scoreLabel=n=>n>=80?'Strong starting point':n>=50?'Developing':'Foundation building';
  const latest=()=>state.latest_completed || (state.assessment?.completed?state.assessment:null) || state.history?.[0] || null;
  function render() {
    ({overview,profile,assessment,path:learningPath,twin,settings,coach}[page] || overview)();
    if (page === 'settings') {
      root.querySelector('#pref-ai').previousElementSibling.querySelector('small').textContent =
      window.BrainMaxI18n.translate('Off by default. When on, your messages and conversation context, goal, latest assessment score, and learning-challenge score and time may be sent to Groq (preferred when configured) or OpenRouter for feedback. When you ask why a path activity was assigned, its saved selection record is also included. Dated score and response-time trends are shared only when history sharing is enabled; retrieved Chroma history is included only when local memory is on.');
    }
  }

  function overview() {
    mode('learning'); const a=latest(); const t=window.BrainMaxI18n.translate;
    if(!a){root.innerHTML=head('YOUR WORKSPACE',`Hello, ${esc(root.dataset.name)}.`, 'Your first six-question assessment will establish a starting point.')+`<div class="bm-panel bm-score-hero"><div><span class="bm-eyebrow" style="color:#c9dfff">YOUR NEXT STEP</span><h2>Find your starting point</h2><p>Choose a subject, answer six questions, and receive a focused learning path.</p>${button(state.profile?.subject?'/assessment/':'/profile/',state.profile?.subject?'Start assessment':'Create learning profile')}</div></div>`;speak('Start with your profile. I’ll help you understand your results and choose a useful next step.','growth-one','👋 Here to help',['Make a plan']);return;}
    const prev=state.preferences?.growth ? state.history?.[1] : null; const delta=prev ? a.score-prev.score : null;
    const openReview=state.collaboration?.profile_review;
    const reviewPanel=openReview?.status==='under_review'?`<section class="bm-panel" style="margin-top:20px"><h2>${t('Assessment under review')}</h2><p>${t('Your score has not changed. You can add fresh evidence with a short verification check.')}</p><button id="bm-start-verification" class="bm-btn secondary" type="button">${t('Start verification')} →</button></section>`:'';
    root.innerHTML=head('YOUR WORKSPACE',`Welcome back, ${esc(root.dataset.name)}.`,`${esc(a.subject_name)} · Latest assessment ${esc(a.date)}`,button('/assessment/','Practise again'))+reviewPanel+`<div class="bm-grid"><section class="bm-panel bm-score-hero"><div><span class="bm-eyebrow" style="color:#c9dfff">CURRENT SCORE</span><div class="bm-kpi">${a.score}<small style="font-size:20px">/100</small></div><h2>${esc(scoreLabel(a.score))}</h2><p>Correct without hints. ${delta===null?'First baseline':`${delta>=0?'+':''}${delta} points from the previous session`}.</p></div></section><section class="bm-panel"><h2>Your next small step</h2><p>${esc(recommendation(a))}</p>${button('/learning-path/','Open learning path',true)}</section></div><div class="bm-grid three" style="margin-top:20px"><div class="bm-panel"><span class="bm-note">ACCURACY</span><div class="bm-kpi">${a.accuracy}%</div><p>Correct answers including hinted ones.</p></div><div class="bm-panel"><span class="bm-note">CONFIDENCE CALIBRATION</span><div class="bm-kpi">${a.calibration}</div><p>How well certainty matched results.</p></div><div class="bm-panel"><span class="bm-note">AVG RESPONSE TIME</span><div class="bm-kpi">${a.avg_time}s</div><p>Context only; never a penalty.</p></div></div>${collaborationPanel(state.collaboration,window.BrainMaxI18n.translate)}<section class="bm-panel" style="margin-top:20px"><h2>Section scores</h2>${topicBars(a)}</section><p class="bm-note" style="margin-top:13px">Six questions are only a small sample. Scores describe this exercise, not a diagnosis or fixed ability.</p>`;
    const verificationButton=root.querySelector('#bm-start-verification');
    if(verificationButton)verificationButton.onclick=()=>runCoachAction({
      type:'verification_retest',
      assessment_id:openReview.latest_assessment_id,
    },verificationButton);
    wireCollaborationPanel(window.BrainMaxI18n.translate);
    speak(`You have a ${a.score}/100 baseline. ${recommendation(a)}`,'growth-three','✅ Done',['Explain my score','My next step']);
  }
  function recommendation(a){const entries=Object.entries(a.topics||{}).sort((x,y)=>x[1]-y[1]);return entries.length?`Review ${entries[0][0]} first, then try one new example without help.`:'Complete an assessment to get a recommendation.';}
  function coach(){
    mode('learning');
    const t=window.BrainMaxI18n.translate;
    const savedLanguage=localStorage.getItem('coachLanguage');
    const language=['en','tn'].includes(savedLanguage)?savedLanguage:'en';
    const a=latest();
    const weakest=a?Object.entries(a.topics||{}).sort((first,second)=>first[1]-second[1])[0]:null;
    const strongest=a?Object.entries(a.topics||{}).sort((first,second)=>second[1]-first[1])[0]:null;
    const providerNote=state.ai_ready
      ?state.preferences?.ai
        ?t('AI coaching is on. Your message and the learning context described in Settings may be sent to the configured AI provider. When you ask why a path activity was assigned, its selection record is included; dated progress trends are shared only when history sharing is on.')
        :t('AI coaching is optional. Enable it in Settings for AI-generated support and feedback.')
      :t('Local support and feedback are available. AI-generated replies need a configured provider.');
    root.innerHTML=`<div class="bm-head coach-page-head"><div><span class="bm-eyebrow">${t('One Step at a Time')}</span><h1>${t('Let’s talk about your progress.')}</h1><p>${t('Ask a question or request a personalized training plan.')}</p></div><div class="coach-language"><label for="coachLanguage">${t('Chat language')}</label><select id="coachLanguage" class="bm-coach-select"><option value="tn" ${language==='tn'?'selected':''}>${t('Tunisian Arabic')}</option><option value="en" ${language==='en'?'selected':''}>${t('English')}</option></select></div></div><div class="coach-layout"><section class="bm-panel coach-chat-panel"><p class="coach-note" id="coachPrivacyNote"></p><div class="coach-messages" id="chatMessages" role="log" aria-live="polite" aria-relevant="additions text"></div><form class="coach-form" id="chatForm"><label class="visually-hidden" for="chatInput">${t('Your message')}</label><input class="coach-input" id="chatInput" maxlength="500" autocomplete="off" placeholder="${t('e.g. How can I improve my focus?')}" required><button class="bm-btn coach-send" type="submit" aria-label="${t('Send')}">${t('Send')} ${pathIcon('arrow')}</button></form><p class="coach-chat-footnote">${t('Tell Brain what you want done: open or start a page, change the next assessment subject, level or topic, adjust your learning path, profile, goals or settings, and pause or resume an assessment. Brain previews changes and waits for your confirmation. Applying an assessment or topic change starts a new assessment and replaces any in-progress assessment; completed results stay unchanged.')}</p><p class="coach-chat-footnote">${t('Your answers stay yours. Brain will not answer assessment questions or submit learning activities for you. Data deletion needs a separate confirmation; data export starts only when you choose the download button.')}</p><p class="coach-chat-footnote">${t('Name the exact concept you want to study today. Choose a reviewed cybersecurity topic such as phishing awareness, password safety, or encryption basics. For another topic, Brain can prepare a focused AI assessment and lesson when AI coaching is enabled.')}</p><p class="coach-chat-footnote">${t('Conversation history stays in this browser session.')}</p><div id="coachError" class="bm-error" role="alert"></div></section><aside class="bm-panel coach-insights"><div class="coach-insights-head"><h2>${t('Profile Insights')}</h2><p>${t('Supportive feedback based on your learning')}</p></div><div class="insight-item"><span class="coach-insight-icon">${pathIcon('spark')}</span><div><h3>${a?t('Your latest practice'):t('Start with one small step')}</h3><p>${a?`${t('Your latest score was')} ${a.score}/100. ${t('That is one practice snapshot, not a label.')}`:t('Complete an assessment and Brain can give feedback grounded in your results.')}</p></div></div><div class="insight-item"><span class="coach-insight-icon blue">${pathIcon('target')}</span><div><h3>${weakest?t('A useful focus'):t('Support at your pace')}</h3><p>${weakest?`${t('Consider practising')} ${esc(weakest[0])}. ${t('Small, steady steps are enough.')}`:t('Ask Brain for a practical plan or Pinky for a little encouragement.')}</p></div></div><div class="insight-item"><span class="coach-insight-icon violet">${pathIcon('book')}</span><div><h3>${strongest?t('Build on what is working'):t('Feedback without pressure')}</h3><p>${strongest?`${t('Your highest topic in this session was')} ${esc(strongest[0])} (${strongest[1]}%). ${t('Use it as a starting point for the next topic.')}`:t('Your coach can help you reflect, make a plan, or choose one manageable next action.')}</p><a class="coach-insight-link" href="/learning-path/">${t('View my learning path')} →</a></div></div></aside></div>`;
    root.querySelector('#coachPrivacyNote').textContent=providerNote;
    const messages=root.querySelector('#chatMessages');
    const appendMessage=(speaker,content,kind='assistant',source='',actions=[])=>{
      const message=document.createElement('article');
      message.className=`coach-message ${kind}`;
      const avatar=document.createElement('span');
      avatar.className='coach-avatar';
      avatar.textContent=speaker;
      const bubble=document.createElement('div');
      bubble.className='coach-bubble';
      const paragraph=document.createElement('p');
      paragraph.textContent=content;
      bubble.append(paragraph);
      if(source){
        const label=document.createElement('small');
        label.className='coach-source';
        label.textContent=source;
        bubble.append(label);
      }
      addCoachActions(bubble,actions);
      message.append(avatar,bubble);
      messages.append(message);
      messages.scrollTop=messages.scrollHeight;
      return message;
    };
    appendMessage('AI',`${t('Hello')}, ${root.dataset.name}! ${t('Would you like to review your latest score or prepare your next session?')}`);
    chatHistory.forEach(item=>appendMessage(
      item.role==='user'?t('You'):'AI',
      item.role==='assistant'?readableCoachMessage(item.content):item.content,
      item.role,
    ));
    root.querySelector('#coachLanguage').onchange=event=>localStorage.setItem('coachLanguage',event.currentTarget.value);
    root.querySelector('#chatForm').onsubmit=async event=>{
      event.preventDefault();
      const input=root.querySelector('#chatInput');
      const send=root.querySelector('.coach-send');
      const text=input.value.trim();
      if(!text||busy)return;
      const inAssessment=page==='assessment'&&Boolean(state.assessment&&!state.assessment.completed);
      const requested=inAssessment?'assessment':state.latest_completed?'debrief':'learning';
      chatHistory.push({role:'user',content:text});
      sessionStorage.setItem(chatStorageKey,JSON.stringify(chatHistory));
      appendMessage(t('You'),text,'user');
      const pending=appendMessage('AI',t('BrainMax is thinking...'),'pending');
      input.value='';
      input.disabled=true;
      send.disabled=true;
      busy=true;
      root.querySelector('#coachError').textContent='';
      try{
        const result=await api('coach',{
          mode:requested,
          message:text,
          mood:selectedMood,
          language:root.querySelector('#coachLanguage').value,
          conversation:chatContext(),
        });
        pending.remove();
        const speaker=result.speaker==='pinky'?'Pinky':'AI';
        const assistantMessage=appendMessage(
          speaker,result.message,'assistant',result.source,result.actions,
        );
        chatHistory.push({
          role:'assistant',
          content:readableCoachMessage(result.message),
        });
        sessionStorage.setItem(chatStorageKey,JSON.stringify(chatHistory));
        const navigation=result.actions?.find(action=>action.type==='navigate_page');
        if(navigation){
          speak(
            `${result.message} Choose the action button to continue.`,
            result.mode==='assessment'?'quiet':'neutral',
            result.source||'READY TO HELP',
            [],
            speaker==='Pinky'?'pinky':'brain'
          );
          return;
        }
        speak(result.message,result.mode==='assessment'?'quiet':'neutral',result.source||'READY TO HELP',[],speaker==='Pinky'?'pinky':'brain');
      }catch(error){
        pending.remove();
        root.querySelector('#coachError').textContent=error.message;
      }finally{
        busy=false;
        input.disabled=false;
        send.disabled=false;
        input.focus();
      }
    };
    speak('Your coach is ready. Tell Brain what you need done or ask Pinky for support.','neutral','👀 Listening');
  }
  function topicBars(a){return Object.entries(a.topics||{}).map(([topic,score])=>`<div class="bm-topic"><div class="bm-topic-line"><span>${esc(topic)}</span><span>${score}%</span></div><div class="bm-progress" role="progressbar" aria-label="${esc(topic)} score" aria-valuenow="${score}" aria-valuemin="0" aria-valuemax="100"><span style="width:${score}%"></span></div></div>`).join('');}
  function trendChart(history){
    const width=760,height=260,pad={top:24,right:20,bottom:48,left:42};
    const innerWidth=width-pad.left-pad.right,innerHeight=height-pad.top-pad.bottom;
    const points=history.map((item,index)=>({x:pad.left+(history.length===1?innerWidth/2:index*innerWidth/(history.length-1)),y:pad.top+(100-item.score)*innerHeight/100,item}));
    const line=points.map(point=>`${point.x},${point.y}`).join(' ');
    const area=`${pad.left},${pad.top+innerHeight} ${line} ${pad.left+innerWidth},${pad.top+innerHeight}`;
    const grid=[0,50,100].map(value=>{const y=pad.top+(100-value)*innerHeight/100;return `<line x1="${pad.left}" y1="${y}" x2="${width-pad.right}" y2="${y}" class="bm-chart-grid"/><text x="4" y="${y+4}" class="bm-chart-axis">${value}</text>`;}).join('');
    const dots=points.map(point=>`<circle cx="${point.x}" cy="${point.y}" r="5" class="bm-chart-dot"><title>${esc(point.item.date)}: ${point.item.score}/100</title></circle>`).join('');
    const labels=[points[0],points[Math.floor((points.length-1)/2)],points[points.length-1]].filter((point,index,all)=>all.findIndex(item=>item.x===point.x)===index).map(point=>`<text x="${point.x}" y="${height-12}" text-anchor="middle" class="bm-chart-axis">${esc(point.item.date)}</text>`).join('');
    return `<div class="bm-chart-wrap"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Score history across ${history.length} completed sessions">${grid}<polygon points="${area}" class="bm-chart-area"/><polyline points="${line}" class="bm-chart-line"/>${dots}${labels}</svg></div>`;
  }
  const formatRadarLabel = value => String(value ?? '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b([a-z])([a-z]*)/g, (_, first, rest) => first.toUpperCase() + rest)
    .replace(/\bAi\b/g, 'AI')
    .replace(/\bMl\b/g, 'ML');
  const wrapRadarLabel = (value, limit = 18) => {
    const words = String(value ?? '').split(/\s+/).filter(Boolean);
    if (!words.length) return [''];
    const lines = [];
    let current = '';
    words.forEach(word => {
      const candidate = current ? `${current} ${word}` : word;
      if (candidate.length > limit && current) {
        lines.push(current);
        current = word;
      } else {
        current = candidate;
      }
    });
    if (current) lines.push(current);
    return lines.length ? lines : [''];
  };
  function skillRadar(topics){
    const entries=Object.entries(topics||{}),width=440,height=360,centerX=width/2,centerY=height/2,radius=88;
    if(entries.length<3)return '<p>Complete a session covering at least three topics to build a skill radar.</p>';
    const point=(index,value)=>{const angle=(index*2*Math.PI/entries.length)-Math.PI/2,scale=radius*value/100;return `${centerX+Math.cos(angle)*scale},${centerY+Math.sin(angle)*scale}`;};
    const rings=[25,50,75,100].map(level=>`<polygon points="${entries.map((_,index)=>point(index,level)).join(' ')}" class="bm-radar-grid"/>`).join('');
    const axes=entries.map((_,index)=>`<line x1="${centerX}" y1="${centerY}" x2="${point(index,100).split(',')[0]}" y2="${point(index,100).split(',')[1]}" class="bm-radar-axis"/>`).join('');
    const labels=entries.map(([topic,score],index)=>{
      const angle=(index*2*Math.PI/entries.length)-Math.PI/2;
      const x=centerX+Math.cos(angle)*(radius+37);
      const y=centerY+Math.sin(angle)*(radius+37);
      const anchor=Math.cos(angle)>.3?'start':Math.cos(angle)<-.3?'end':'middle';
      const lines=wrapRadarLabel(formatRadarLabel(topic));
      const textLines=[...lines,`${score}%`];
      return `<text x="${x}" y="${y-((textLines.length-1)*6)}" text-anchor="${anchor}" class="bm-radar-label">${textLines.map((text,lineIndex)=>`<tspan x="${x}" dy="${lineIndex===0?0:13}" class="${lineIndex===textLines.length-1?'bm-radar-score':''}">${esc(text)}</tspan>`).join('')}</text>`;
    }).join('');
    return `<div class="bm-radar-wrap"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Latest session topic scores, shown separately for each topic from zero to one hundred.">${rings}${axes}<polygon points="${entries.map(([,score],index)=>point(index,score)).join(' ')}" class="bm-radar-value"/>${entries.map(([,score],index)=>{const [cx,cy]=point(index,score).split(',');return `<circle cx="${cx}" cy="${cy}" r="4" class="bm-radar-dot"><title>${esc(entries[index][0])}: ${score}%</title></circle>`;}).join('')}${labels}</svg></div>`;
  }
  function dimensionRadarPanel(path,t){
    const dimensions=path.dimensions||[],radar=path.radar||{};
    if(!dimensions.length)return '';
    const width=640,height=520,cx=width/2,cy=height/2,radius=150;
    const axes=radar.axes||dimensions.map(item=>item.name);
    const point=(index,value)=>{
      const angle=(index*2*Math.PI/axes.length)-Math.PI/2;
      const scale=radius*Math.max(0,Math.min(100,Number(value)||0))/100;
      return `${cx+Math.cos(angle)*scale},${cy+Math.sin(angle)*scale}`;
    };
    const polygon=values=>values.map((value,index)=>point(index,value??0)).join(' ');
    const rings=[25,50,75,100].map(level=>`<polygon points="${polygon(axes.map(()=>level))}" class="bm-radar-grid"/>`).join('');
    const axisLines=axes.map((_,index)=>`<line x1="${cx}" y1="${cy}" x2="${point(index,100).split(',')[0]}" y2="${point(index,100).split(',')[1]}" class="bm-radar-axis"/>`).join('');
    const uncertainty=radar.uncertainty||[];
    const bandPoints=[
      ...uncertainty.map((item,index)=>point(index,item.upper)),
      ...uncertainty.map((item,index)=>point(uncertainty.length-1-index,uncertainty[uncertainty.length-1-index].lower)),
    ].join(' ');
    const previous=(radar.previous||[]).filter(value=>value!==null&&value!==undefined);
    const previousLine=previous.length===axes.length
      ?`<polygon points="${polygon(radar.previous)}" class="bm-radar-previous"/>`
      :'';
    const targetLine=`<polygon points="${polygon(radar.target||axes.map(()=>70))}" class="bm-radar-target"/>`;
    const valuesLine=`<polygon points="${polygon(radar.values||[])}" class="bm-radar-value"/>`;
    const labels=axes.map((name,index)=>{
      const angle=(index*2*Math.PI/axes.length)-Math.PI/2;
      const x=cx+Math.cos(angle)*(radius+54),y=cy+Math.sin(angle)*(radius+54);
      const anchor=Math.cos(angle)>.3?'start':Math.cos(angle)<-.3?'end':'middle';
      const labelText=formatRadarLabel(name);
      const labelLines=wrapRadarLabel(labelText, 16);
      const textLines=[...labelLines, `${Number(radar.values?.[index]||0).toFixed(1)}%`];
      return `<text x="${x}" y="${y-((textLines.length-1)*10)}" text-anchor="${anchor}" class="bm-radar-label">${textLines.map((text,lineIndex)=>`<tspan x="${x}" dy="${lineIndex===0?0:14}" class="${lineIndex===textLines.length-1?'bm-radar-score':''}">${esc(text)}</tspan>`).join('')}</text>`;
    }).join('');
    const legend=`<div class="bm-dimension-legend"><span><i class="current"></i>${t('Current')}</span><span><i class="target"></i>${t('Practice target')}</span>${previousLine?`<span><i class="previous"></i>${t('Previous snapshot')}</span>`:''}<span><i class="uncertainty"></i>${t('Uncertainty range')}</span></div>`;
    const provisional=path.asymmetry?.verified
      ?t('Profile checks completed across repeated sessions.')
      :t('Provisional profile: more evidence is needed before confirming a persistent difference.');
    const checkLabels={
      ceiling:{
        stretch_item_present:'A challenging item was included.',
        probe_upward_before_confirming:'A harder item is needed to check the upper level.',
        not_checked:'Not checked yet.',
      },
      floor:{
        response_format_signals_available:'Response pacing supports the current format.',
        try_an_easier_or_clearer_variant:'Try an easier or clearer format before drawing a conclusion.',
        not_checked:'Not checked yet.',
      },
      context:{
        context_signals_reviewed:'Available context signals were reviewed.',
        context_or_fatigue_signals_uncertain:'Context or fatigue may still affect this result.',
        no_attention_sensitive_dimension:'No attention-sensitive dimension was identified.',
        not_checked:'Not checked yet.',
      },
    };
    const checks=Object.entries(path.asymmetry?.checks||{}).map(([key,value])=>`<li><strong>${t(key)}:</strong> ${t(checkLabels[key]?.[value]||String(value).replaceAll('_',' '))}</li>`).join('');
    const verificationNote=checks?`<details class="bm-dimension-checks"><summary>${t('How this profile was checked')}</summary><ul>${checks}</ul></details>`:'';
    const dimensionForecasts=dimensions.map(item=>{
      const forecast=item.forecast;
      if(!forecast||forecast.data_sufficiency==='insufficient')return '';
      return `<li><strong>${esc(item.name)}:</strong> ${t('next-practice range')} ${forecast.low}–${forecast.high} · ${t(forecast.confidence||'low confidence')}</li>`;
    }).filter(Boolean).join('');
    const forecastPanel=dimensionForecasts
      ?`<h3>${t('Separate topic projections')}</h3><p>${t('Each range is estimated independently; they are not combined into one score.')}</p><ul class="bm-dimension-alt">${dimensionForecasts}</ul>`
      :`<p class="bm-note">${t('More sessions are needed for separate topic projections.')}</p>`;
    return `<section class="bm-panel bm-dimension-panel"><span class="bm-eyebrow">${t('SKILL PROFILE · TOPICS ARE SEPARATE')}</span><h2>${t('A different pace for each topic')}</h2><p>${esc(path.pinky_message||provisional)}</p>${radar.annotation?`<p class="bm-dimension-annotation">${esc(radar.annotation)}</p>`:''}<div class="bm-radar-wrap"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(radar.alt_text||axes.join(', '))}. Each axis is a separate topic; polygon area is not a total score.">${rings}${axisLines}<polygon points="${bandPoints}" class="bm-radar-uncertainty"/>${previousLine}${targetLine}${valuesLine}${labels}</svg></div>${legend}<p class="bm-radar-caption">${t('Each axis represents a separate topic. The chart shape is not a combined score; read each topic value below.')}</p><ul class="bm-dimension-alt">${dimensions.map(item=>`<li><strong>${esc(item.name)}:</strong> ${(item.theta*100).toFixed(1)}% · ${t(item.level)} · ${t('Next difficulty')}: ${t(({1:'Foundation',2:'Building',3:'Stretch'})[item.difficulty_target]||'Foundation')} · ${Math.round(item.time_share*100)}% ${t('practice time')}</li>`).join('')}</ul>${forecastPanel}${verificationNote}<p class="bm-note">${provisional}</p></section>`;
  }
  function activityHeatmap(activity){
    if(!activity?.length)return '<p>Complete a session to start your practice activity history.</p>';
    const cells=activity.map(day=>`<span class="bm-heat-cell level-${Math.min(4,day.count)}" title="${esc(day.date)} · ${day.count} completed session${day.count===1?'':'s'}" aria-label="${esc(day.date)}: ${day.count} sessions"></span>`).join('');
    return `<div class="bm-heatmap-wrap"><div class="bm-heatmap" role="img" aria-label="Completed assessment activity over the last 12 weeks">${cells}</div><div class="bm-heatmap-legend"><span>Less</span><i class="level-0"></i><i class="level-1"></i><i class="level-2"></i><i class="level-3"></i><i class="level-4"></i><span>More</span></div></div>`;
  }

  function profile(){mode('learning');draft={name:root.dataset.name,...state.profile,...draft};root.innerHTML=head('YOUR LEARNING PROFILE','A path shaped around you.','Tell us what you want to learn. Brain will guide each step.')+`<div class="bm-grid"><form id="profile-form" class="bm-panel"><h2>About you</h2><div class="bm-field"><label for="bm-name">What should we call you?</label><input id="bm-name" name="name" maxlength="80" value="${esc(draft.name)}" required></div><div class="bm-field"><label for="bm-subject">What would you like to learn?</label><select id="bm-subject" name="subject" required><option value="">Choose a subject</option>${Object.entries(subjects).map(([k,v])=>`<option value="${k}" ${draft.subject===k?'selected':''}>${v}</option>`).join('')}</select></div><div class="bm-field"><label for="bm-occupation">Your current situation</label><select id="bm-occupation" name="occupation" required><option value="">Choose one</option>${['Student','Professional','Exploring a new field'].map(x=>`<option ${draft.occupation===x?'selected':''}>${x}</option>`).join('')}</select></div><div class="bm-field"><label for="bm-motivation">Your goal</label><textarea id="bm-motivation" name="motivation" maxlength="500" placeholder="For example: build my first AI project" required>${esc(draft.motivation)}</textarea></div><div class="bm-grid"><div class="bm-field"><label for="bm-experience">Starting level</label><select id="bm-experience" name="experience" required><option value="">Choose one</option>${['New to this','Some foundations','Comfortable','Advanced'].map(x=>`<option ${draft.experience===x?'selected':''}>${x}</option>`).join('')}</select></div><div class="bm-field"><label for="bm-minutes">Minutes per day</label><select id="bm-minutes" name="minutes" required><option value="">Choose one</option>${['15','30','60'].map(x=>`<option value="${x}" ${draft.minutes===x?'selected':''}>${x} minutes</option>`).join('')}</select></div></div><div id="bm-error" class="bm-error" role="alert"></div><div class="bm-form-actions"><button class="bm-btn" type="submit">Save profile & start assessment →</button></div></form><aside class="bm-panel"><span class="bm-tag">BRAIN · learning mode</span><h2 style="margin-top:18px">One step at a time</h2><p>Start with a subject and a goal. You can change these later.</p><p>Your assessment includes six questions selected from a reviewed question pool. Brain will help you review definitions and results when you finish.</p><div class="bm-panel" style="background:#f2f7ff;box-shadow:none"><strong>Privacy</strong><p style="margin:6px 0 0">Your profile and answers stay linked to your account. AI sharing is off until you enable it in Settings.</p></div></aside></div>`;
    root.querySelectorAll('input,select,textarea').forEach(el=>el.addEventListener('change',()=>{draft[el.name]=el.value;const messages={name:'Name saved in this form. Next, choose your subject.',subject:'Great. Now tell me what you want to achieve.',occupation:'That helps me make the next step practical.',motivation:'Your goal gives this learning path a direction.',experience:'A starting level helps us set the right pace.',minutes:'Short, steady sessions work well. Save when you are ready.'};speak(messages[el.name]||'Next step is ready.','neutral','✅ Done');}));
    root.querySelector('form').onsubmit=e=>{e.preventDefault();root.querySelectorAll('[name]').forEach(el=>draft[el.name]=el.value.trim());task(async()=>{await api('profile',draft);state.profile={...draft};speak('Your profile is ready. Complete your assessment and I’ll take you to your learning path.','growth-one','✅ Ready');window.location.href='/assessment/';});};
    speak('Choose a subject and share your goal. I’ll help you make the next step easy.','neutral','👀 Listening',['Make a plan']);
  }

  function showPinkyEngagement(result,assessmentId){
    root.querySelector('#bm-engagement-panel')?.remove();
    const paused=Boolean(result.ui?.pause_session);
    if(state.assessment)state.assessment.engagement_paused=paused;
    const next=root.querySelector('#bm-next');
    if(next)next.disabled=paused;
    if(result.action==='END_OFFER'&&!result.ui?.buttons?.length){
      window.location.href='/dashboard/';
      return;
    }
    if(!result.message&&!paused)return;
    const panel=document.createElement('section');
    panel.id='bm-engagement-panel';
    panel.className='bm-panel bm-engagement-panel';
    panel.setAttribute('role','status');
    panel.setAttribute('aria-live','polite');
    const heading=document.createElement('strong');
    heading.textContent='Pinky · check-in';
    const message=document.createElement('p');
    message.textContent=result.message||'Your assessment is paused. Choose when you are ready to continue.';
    panel.append(heading,message);
    const choices=document.createElement('div');
    choices.className='bm-engagement-actions';
    const sendReply=reply=>task(async()=>{
      const response=await api('engagement',{
        assessment_id:assessmentId,
        user_reply:reply,
      });
      if(state.assessment)state.assessment.engagement_paused=Boolean(response.ui?.pause_session);
      showPinkyEngagement(response,assessmentId);
      speak(response.message,'quiet','💗 Pinky is here',[], 'pinky');
    });
    (result.ui?.buttons||[]).forEach(label=>{
      const action=document.createElement('button');
      action.type='button';
      action.className='bm-btn secondary';
      action.textContent=label;
      action.onclick=()=>sendReply(label);
      choices.append(action);
    });
    if(paused&&!result.ui?.buttons?.length){
      const resume=document.createElement('button');
      resume.type='button';
      resume.className='bm-btn secondary';
      resume.textContent='Resume assessment';
      resume.onclick=()=>sendReply('resume');
      choices.append(resume);
    }
    if(choices.childElementCount)panel.append(choices);
    root.querySelector('.bm-progress')?.after(panel);
    if(paused&&next)next.disabled=true;
  }
  function assessment(){const a=state.assessment;if(!state.profile?.subject){root.innerHTML=head('ASSESSMENT','Start with your profile.','Choose a subject so I can prepare six focused questions.')+`<div class="bm-panel">${button('/profile/','Set up profile')}</div>`;mode('learning');speak('First, choose your subject in Profile.','neutral','👀 Listening');return;}
    if(a?.completed){debrief(a);return;}
    if(!a){mode('learning');root.innerHTML=head('ASSESSMENT',`${esc(subjects[state.profile.subject])} baseline`,'Six questions selected from a reviewed pool. No time limit; take each question at your own pace.')+`<div class="bm-panel"><span class="bm-tag">READY</span><h2 style="margin-top:16px">Find your starting point</h2><p>Choose an answer and confidence for each question. Brain will help you review definitions and results when you finish.</p><button id="bm-start" class="bm-btn">Start assessment →</button><div id="bm-error" class="bm-error"></div></div>`;root.querySelector('#bm-start').onclick=()=>task(async()=>{state.assessment=await api('assessment',{});assessment();});speak('Let’s find your starting point. I’ll help you make sense of the results when you’re done.','growth-one','👋 Here to help');return;}
    mode('assessment');choice=null;confidence=null;const n=a.answers_count+1, q=a.question, section=Math.min(3,Math.ceil(n/Math.max(1,Math.ceil(a.total/3))));root.innerHTML=head('ASSESSMENT',`${esc(a.subject_name)} adaptive practice`,'Difficulty is adjusted from your profile and previous topic results; it can move up or down as you practise.')+`<div class="bm-qhead"><span class="bm-tag">Section ${section} of 3 · ${esc(q.topic)} · ${esc(a.difficulty||'Foundation')}</span><strong>Question ${n} of ${a.total}</strong></div><div class="bm-progress"><span style="width:${Math.round(a.answers_count/a.total*100)}%"></span></div><div class="bm-panel" style="margin-top:20px"><div class="bm-question">${esc(q.prompt)}</div><div class="bm-options">${q.choices.map((v,i)=>`<button class="bm-option" data-choice="${i}" type="button" aria-pressed="false"><b>${'ABCD'[i]}</b>${esc(v)}</button>`).join('')}</div><p id="bm-selected-choice" class="bm-selected-choice" aria-live="polite">Choose one answer.</p><div class="bm-field" style="margin-top:24px"><label>How confident are you?</label><div class="bm-confidence">${[25,50,75,100].map(v=>`<button class="bm-choice" type="button" data-confidence="${v}" aria-pressed="false">${v}%</button>`).join('')}</div></div><div id="bm-error" class="bm-error" role="alert"></div><div class="bm-form-actions"><span class="bm-note">Results and concept explanations appear when you finish.</span><button id="bm-next" class="bm-btn">${n===a.total?'Submit assessment':'Save & next question'} →</button></div></div>`;
    if(q.requeued){
      const sectionTag=root.querySelector('.bm-qhead .bm-tag');
      if(sectionTag)sectionTag.textContent=`Retry flagged question · ${q.topic} · ${a.difficulty||'Foundation'}`;
    }
    root.querySelectorAll('[data-choice]').forEach(b=>b.onclick=()=>{choice=Number(b.dataset.choice);root.querySelectorAll('[data-choice]').forEach(x=>{x.classList.toggle('selected',x===b);x.setAttribute('aria-pressed',String(x===b));});root.querySelector('#bm-selected-choice').textContent=`Selected: ${q.choices[choice]}`;});
    root.querySelectorAll('[data-confidence]').forEach(b=>b.onclick=()=>{confidence=Number(b.dataset.confidence);root.querySelectorAll('[data-confidence]').forEach(x=>{x.classList.toggle('selected',x===b);x.setAttribute('aria-pressed',String(x===b));});});
    questionTelemetry={startedAt:performance.now(),stimulus_seen:false,tab_blurred:false};
    lastQuestionActivity=questionTelemetry.startedAt;
    root.querySelector('.bm-question').addEventListener('pointerenter',()=>{questionTelemetry.stimulus_seen=true;});
    root.querySelector('#bm-next').onclick=()=>{if(choice===null||confidence===null){root.querySelector('#bm-error').textContent='Choose an answer and confidence level.';return;}const now=performance.now();const telemetry={active_ms:Math.max(0,Math.round(now-questionTelemetry.startedAt)),stimulus_seen:questionTelemetry.stimulus_seen,tab_blurred:questionTelemetry.tab_blurred,inactive_gap:now-lastQuestionActivity>15000};task(async()=>{try{state.assessment=await api('answer',{assessment_id:a.id,index:a.answers_count,choice,confidence,telemetry});}catch(e){if(e.status!==409)throw e;state=await api('state');assessment();return;}questionTelemetry=null;if(state.assessment.completed){debrief(state.assessment);}else{const intervention=state.assessment.engagement_intervention;assessment();if(intervention){showPinkyEngagement(intervention,state.assessment.id);speak(intervention.message,'quiet','💗 Pinky is here',[],'pinky');}else if(n%2===0)speak(`Question ${n} saved. You’re making steady progress.`, 'quiet','🌱 Progress saved');}});};
    if(a.engagement_paused){
      const currentIntervention=a.engagement_intervention||{
        action:'CHECK_IN',
        message:'Your assessment is paused. Choose when you are ready to continue.',
        ui:{pause_session:true,buttons:[]},
      };
      showPinkyEngagement(currentIntervention,a.id);
      speak(currentIntervention.message,'quiet','💗 Pinky is here',[],'pinky');
    }else{
      speak(n===1?'Let’s take this one question at a time. Choose what feels right for you.':`Question ${n} of ${a.total}. Take your time; there’s no clock running.`, 'quiet','👋 Here to help');
    }
  }
  function debrief(a){mode('debrief');const weakest=Object.entries(a.topics).sort((x,y)=>x[1]-y[1])[0];const title=a.verification_for?'Your verification check, explained.':'Your starting point, explained.';root.innerHTML=head('ASSESSMENT COMPLETE',title,`${esc(a.subject_name)} · ${esc(a.date)}`,`<button id="bm-retake" class="bm-btn secondary">Take another assessment →</button>`)+`<div class="bm-grid bm-result-overview"><section class="bm-panel bm-score-hero"><div class="bm-score-copy"><span class="bm-eyebrow">SESSION SCORE</span><div class="bm-score-number">${a.score}<small>/100</small></div><div class="bm-score-progress" role="progressbar" aria-label="Score" aria-valuenow="${a.score}" aria-valuemin="0" aria-valuemax="100"><span style="width:${a.score}%"></span></div><h2>${esc(a.level)}</h2><p>Correct answers without hints ÷ ${a.total} × 100.</p></div></section><section class="bm-panel bm-result-topics"><div class="bm-result-card-heading"><span class="bm-tag">QUICK SNAPSHOT</span><h2>Section breakdown</h2><p>${a.total} questions in this session; treat these as a small snapshot.</p></div>${topicBars(a)}</section></div><section class="bm-result-metrics"><div class="bm-result-section-heading"><span class="bm-eyebrow">A CLOSER LOOK</span><h2>What this score means</h2><p>A few helpful details from this session.</p></div><div class="bm-grid three"><article class="bm-panel bm-metric-card"><span class="bm-metric-icon" aria-hidden="true">01</span><div><span class="bm-note">Accuracy</span><div class="bm-kpi">${a.accuracy}%</div><p>Answers correct, including those with hints.</p></div></article><article class="bm-panel bm-metric-card"><span class="bm-metric-icon" aria-hidden="true">02</span><div><span class="bm-note">Confidence alignment</span><div class="bm-kpi">${a.calibration}<small>/100</small></div><p>How closely confidence matched your answers.</p></div></article><article class="bm-panel bm-metric-card"><span class="bm-metric-icon" aria-hidden="true">03</span><div><span class="bm-note">Independent answers</span><div class="bm-kpi">${a.independence}%</div><p>Questions answered without a hint.</p></div></article></div></section><section class="bm-panel bm-next-step"><span class="bm-eyebrow">SUGGESTED NEXT STEP</span><h2>Brain’s first recommendation</h2><p>Review <strong>${esc(weakest[0])}</strong>, then practise a new example at your own pace.</p><span class="bm-note">Based on your latest ${a.total} answers. This is not a validated cognitive measure.</span></section><section class="bm-panel bm-answer-review"><div class="bm-result-section-heading"><span class="bm-eyebrow">LEARN FROM EACH QUESTION</span><h2>Review your answers</h2><p>Open a question to see your answer and explanation. Ask Brain to define your choice if you’d like more context.</p></div>${a.review.map((r,i)=>`<details class="bm-review"><summary><span class="bm-review-number">${String(i+1).padStart(2,'0')}</span><span class="bm-review-question">${esc(r.prompt)}</span><span class="bm-review-status ${r.correct?'is-correct':'needs-review'}">${r.correct?'Correct':'Review'}</span></summary><div class="bm-review-content"><p><span>Your answer:</span> ${esc(r.chosen)}<br><span>Expected:</span> ${esc(r.expected)}</p><p>${esc(r.explanation)}</p><p class="bm-review-meta"><small>Confidence ${r.confidence}% · ${r.seconds}s · ${r.hint?'Hint used':'No hint'}</small></p><button class="bm-btn secondary" type="button" data-definition="${i}">Define my choice</button><p class="bm-definition" id="bm-definition-${i}" aria-live="polite"></p></div></details>`).join('')}</section><p class="bm-note bm-result-footnote">Questions are randomized from a reviewed pool. Identical sets are avoided across learners while combinations remain available; each learner sees no repeats per section until its question pool is exhausted.</p>`;
    if(a.plateau?.intervention_required){
      root.querySelector('.bm-next-step')?.insertAdjacentHTML('beforebegin',`<aside class="bm-pinky-correction bm-plateau-coaching" role="status"><strong>Pinky · A fresh approach</strong><p>${esc(a.plateau.message)}</p></aside>`);
    }
    if(a.reliable_answer_count<a.validity_total){
      const quarantined=a.validity_total-a.reliable_answer_count;
      const note=document.createElement('p');
      note.className='bm-note bm-reliability-note';
      note.textContent=`Result based on ${a.reliable_answer_count} of ${a.validity_total} reliable responses. ${quarantined} low-confidence responses were excluded without penalty; the confidence range was widened.`;
      root.querySelector('.bm-score-copy')?.append(note);
      const formula=root.querySelector('.bm-score-copy p');
      if(formula)formula.textContent=`Correct answers without hints ÷ ${a.reliable_answer_count} reliable responses × 100.`;
      a.review.forEach((row,index)=>{
        if(row.validity!=='low_confidence')return;
        const detail=root.querySelectorAll('.bm-review')[index];
        const status=detail?.querySelector('.bm-review-status');
        if(status){status.textContent='Not scored · low confidence';status.classList.add('needs-review');}
        const content=detail?.querySelector('.bm-review-content');
        if(content){
          const label=document.createElement('p');
          label.className='bm-note';
          label.textContent='This response was set aside as unreliable and did not lower your result.';
          content.prepend(label);
        }
      });
    }
    const retakeButton = root.querySelector('#bm-retake');
    if (retakeButton) {
      retakeButton.type = 'button';
      retakeButton.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
        window.location.assign('/assessment/?start=1');
      };
    }
    const t=window.BrainMaxI18n.translate;
    const overview=root.querySelector('.bm-result-overview');
    const topicSummary=overview?.querySelector('.bm-result-topics')?.outerHTML;
    if(overview){
      overview.outerHTML=`${pathBaselinePanel(a,t)}${topicSummary||''}`;
      root.querySelector('.bm-result-metrics')?.remove();
    }
    const nextStep=root.querySelector('.bm-next-step');
    nextStep?.insertAdjacentHTML('beforeend',button('/learning-path/',t('Continue to your learning path'),true));
    root.querySelectorAll('[data-definition]').forEach(button=>button.onclick=()=>task(async()=>{button.disabled=true;const index=Number(button.dataset.definition);try{const result=await api('definition',{assessment_id:a.id,index});const message=`${result.concept}. ${result.definition}`;root.querySelector(`#bm-definition-${index}`).textContent=message;speak(message,'growth-one','📘 Concept defined');}finally{button.disabled=false;}}));
    if(a.plateau?.intervention_required)speak(a.plateau.message,'neutral','💗 Pinky suggests a fresh approach',[],'pinky');
    else speak(`Assessment complete. You scored ${a.score}/100. Review ${weakest[0]} first, then ask me to define any choice.`,a.score>=80?'confident':'growth-one','✅ Results ready',['Explain my score','My next step']);
  }
  function pathBaselinePanel(baseline,t){
    if(!baseline)return '';
    const weakest=Object.entries(baseline.topics||{}).sort((a,b)=>a[1]-b[1])[0];
    return `<section class="bm-path-baseline"><div class="bm-panel bm-score-hero"><div class="bm-score-copy"><span class="bm-eyebrow">${t('LATEST ASSESSMENT')}</span><div class="bm-score-number">${esc(baseline.score)}<small>/100</small></div><div class="bm-score-progress" role="progressbar" aria-label="${t('Baseline score')}" aria-valuenow="${baseline.score}" aria-valuemin="0" aria-valuemax="100"><span style="width:${baseline.score}%"></span></div><h2>${esc(t(baseline.level))}</h2><p>${t('Status based on your completed assessment.')}</p></div></div><div class="bm-path-metrics"><article class="bm-panel"><span class="bm-note">${t('Answer accuracy')}</span><strong>${esc(baseline.accuracy)}%</strong></article><article class="bm-panel"><span class="bm-note">${t('Confidence calibration')}</span><strong>${esc(baseline.calibration)} / 100</strong></article><article class="bm-panel"><span class="bm-note">${t('Independent answers')}</span><strong>${esc(baseline.independence)}%</strong></article><article class="bm-panel"><span class="bm-note">${t('Average response time')}</span><strong>${esc(baseline.avg_time)}s</strong></article>${weakest?`<article class="bm-panel bm-path-focus"><span class="bm-note">${t('Suggested topic to revisit')}</span><strong>${esc(weakest[0])}</strong><small>${esc(weakest[1])}% ${t('in this short baseline')}</small></article>`:''}</div></section>`;
  }
  const collaborationDimensions=[
    ['clarification','Clarification',20,'I ask questions when an AI request or response is unclear.'],
    ['critical_verification','Critical verification',25,'I check AI responses before relying on them.'],
    ['iteration','Iteration',20,'I refine prompts or results when the first answer is not useful.'],
    ['autonomy','Autonomy',20,'I can work independently without over-relying on AI.'],
    ['responsible_use','Responsible use',15,'I protect privacy and use AI appropriately.'],
  ];
  function collaborationPanel(collaboration,t){
    const data=collaboration||{};
    const score=data.score===null||data.score===undefined?'—':`${data.score}/100`;
    const componentCards=collaborationDimensions.map(([key,label,weight])=>
     `<article class="bm-collaboration-component" data-collaboration-component="${key}"><span>${t(label)}</span><strong>${data.components?.[key]===undefined?'—':`${data.components[key]}%`}</strong><small>${weight}% ${t('weight')}</small></article>`
    ).join('');
    const autonomyNote=data.autonomy_evidence===null||data.autonomy_evidence===undefined
     ?t('No collaboration results are available yet.')
     :`${t('Autonomy also reflects')} ${data.autonomy_evidence}% ${t('unhinted answers in your latest assessment.')}`;
    const challengeNote=data.challenge_credit?.challenge_count
      ?`<p class="bm-note">${t('Evidence-based review credit')}: +${data.challenge_credit.critical_verification} ${t('critical verification')} · +${data.challenge_credit.iteration} ${t('iteration')}.</p>`
      :'';
    const review=data.profile_review||{};
    const affectedQuestions=(review.affected_questions||[]).map(esc).join(', ');
    const reviewNote=review.status==='under_review'
      ?`<div class="bm-note"><p>${t('Your assessment is under review. Its score has not changed.')}</p>${affectedQuestions?`<p>${t('Affected questions')}: ${affectedQuestions}</p>`:''}<p>${t('Challenge quality')}: ${Math.round((review.challenge_quality||0)*100)}% · ${t('Credibility')}: ${Math.round((review.credibility||0)*100)}% · ${t('Uncertainty change')}: ×${esc(review.uncertainty_multiplier||1)} · ${t('Item weight')}: ${Math.round((review.item_weight||1)*100)}%.</p><p>${t('AI stance integrity')}: ${esc(review.ai_stance_integrity??100)}% · ${t('Claim-reliability prior')}: ${Math.round((review.claim_reliability_prior??0.5)*100)}%.</p></div>`
      :'';
    const ratings=collaborationDimensions.map(([key,label,,description])=>{
      const value=data.ratings?.[key]??50;
      return `<label class="bm-collaboration-rating" for="bm-collaboration-${key}"><span>${t(label)}</span><output id="bm-collaboration-${key}-value" for="bm-collaboration-${key}">${esc(value)}</output><input id="bm-collaboration-${key}" name="${key}" type="range" min="0" max="100" step="1" value="${esc(value)}" data-collaboration-rating><small>${t(description)}</small></label>`;
    }).join('');
    return `<section class="bm-panel bm-collaboration-panel"><div class="bm-collaboration-heading"><div><span class="bm-eyebrow">${t('PROVISIONAL · NOT VALIDATED')}</span><h2>${t('AI Collaboration Score')}</h2><p>${t('A reflective snapshot of how you work with AI—not a measure of ability.')}</p></div><div class="bm-collaboration-score"><strong>${score}</strong><span>${t('provisional score')}</span></div></div><div class="bm-collaboration-components">${componentCards}</div><p class="bm-note">${autonomyNote} ${t('This is a design proposal, not a validated measure.')}</p>${challengeNote}${reviewNote}<form id="bm-collaboration-form" class="bm-collaboration-form"><p>${t('Rate how you work with AI on each area, from 0 to 100. Your score uses these ratings and your latest assessment.')}</p><div class="bm-collaboration-ratings">${ratings}</div><div id="bm-collaboration-status" class="bm-collaboration-status" role="status" aria-live="polite">${t('Changes save automatically.')}</div></form></section>`;
  }
  function wireCollaborationPanel(t){
    const form=root.querySelector('#bm-collaboration-form');
    if(!form)return;
    const panel=form.closest('.bm-collaboration-panel');
    const status=form.querySelector('#bm-collaboration-status');
    let saveTimer=null;
    let revision=0;
    let saving=false;
    const ratings=()=>Object.fromEntries(
      [...form.querySelectorAll('[data-collaboration-rating]')]
        .map(input=>[input.name,Number(input.value)]),
    );
    const updateSummary=collaboration=>{
      const score=panel.querySelector('.bm-collaboration-score strong');
      if(score)score.textContent=collaboration.score===null?'—':`${collaboration.score}/100`;
      for(const [key] of collaborationDimensions){
        const value=panel.querySelector(`[data-collaboration-component="${key}"] strong`);
        if(value)value.textContent=collaboration.components?.[key]===undefined?'—':`${collaboration.components[key]}%`;
      }
    };
    const save=async()=>{
      if(saving)return;
      saving=true;
      const savedRevision=revision;
      status.classList.remove('bm-error');
      status.textContent=t('Saving reflection...');
      try{
        const result=await api('collaboration',ratings());
        state.collaboration=result.collaboration;
        if(savedRevision===revision){
          updateSummary(result.collaboration);
          status.textContent=t('Reflection saved.');
        }
      }catch(error){
        if(savedRevision===revision){
          status.classList.add('bm-error');
          status.textContent=error.message;
        }
      }finally{
        saving=false;
        if(savedRevision!==revision)save();
      }
    };
    form.onsubmit=event=>{
      event.preventDefault();
    };
    form.querySelectorAll('[data-collaboration-rating]').forEach(input=>input.addEventListener('input',()=>{
      form.querySelector(`#${input.id}-value`).value=input.value;
      revision++;
      window.clearTimeout(saveTimer);
      status.classList.remove('bm-error');
      status.textContent=t('Changes save automatically.');
      saveTimer=window.setTimeout(save,400);
    }));
  }
  function hebaExercisePanel(exercise,result,t){
    if(!exercise)return '';
    const statusText={
      passed:t('Passed'),
      needs_review:t('Needs practice'),
      in_progress:t('In progress'),
    }[exercise.status]||t('In progress');
    const resultLine=result?.exercise_result
      ?`<div class="bm-heba-feedback ${result.exercise_result.correct?'is-correct':'needs-review'}" role="status"><strong>${result.exercise_result.correct?t('Correct answer'):t('Review this answer')}</strong><span>${t('Score')}: ${result.exercise_result.score}%</span><p>${esc(result.exercise_result.explanation)}</p></div>`
      :'';
    const score=exercise.score===null?'—':`${exercise.score}%`;
    const sourceText=exercise.source==='ai'
      ?t('AI-generated practice questions.')
      :t('Reviewed practice examples matched to your subject.');
    const heading=`<div class="bm-heba-heading"><div><span class="bm-eyebrow">${t('SUBJECT PRACTICE')}</span><h2>${t('Practice questions')}</h2><p>${sourceText}</p></div><div class="bm-heba-status ${exercise.status}"><strong>${esc(score)}</strong><span>${statusText}</span><small>${exercise.answered}/${exercise.total} ${t('answered')}</small></div></div>`;
    if(exercise.completed){
      const review=exercise.review.map((item,index)=>`<details class="bm-review"><summary><span class="bm-review-number">${String(index+1).padStart(2,'0')}</span><span class="bm-review-question">${esc(item.topic)}</span><span class="bm-review-status ${item.correct?'is-correct':'needs-review'}">${item.correct?t('Correct answer'):t('Review')}</span></summary><div class="bm-review-content"><p>${t('Your answer')}: <strong>${esc(Array.isArray(item.answer)?item.answer.join(' → '):item.answer)}</strong><br>${t('Expected')}: <strong>${esc(Array.isArray(item.expected)?item.expected.join(' → '):item.expected)}</strong></p><p>${esc(item.explanation)}</p></div></details>`).join('');
      return `<section class="bm-panel bm-heba-exercise">${heading}<div class="bm-heba-content"><div class="bm-heba-complete ${exercise.passed?'is-passed':'needs-review'}" role="status"><strong>${exercise.passed?t('Exercise set passed'):t('More practice recommended')}</strong><p>${t('Final exercise score')}: <strong>${esc(exercise.score)}%</strong> · ${exercise.attempts} ${t('questions answered')}</p><span>${exercise.passed?t('You met the 70% practice target.'):t('Review the explanations, then start a new challenge when ready.')}</span></div><div class="bm-heba-review">${review}</div></div></section>`;
    }
    const q=exercise.question;
    let activity='';
    if(q.exercise_type==='memory'){
      activity=`<p class="bm-heba-prompt">${esc(q.prompt)}</p><div id="bm-heba-sequence" class="bm-heba-sequence">${q.sequence.map((item,index)=>`<span><small>${index+1}</small>${esc(item)}</span>`).join('')}</div><button id="bm-heba-memory-start" class="bm-btn secondary" type="button">${t('Hide sequence & recall')}</button><div id="bm-heba-recall" hidden><p>${t('Your recalled sequence')} <span id="bm-heba-memory-count">0 / ${q.choices.length}</span></p><ol id="bm-heba-memory-answer"></ol><div class="bm-heba-options">${q.choices.map((item,index)=>`<button class="bm-heba-option" type="button" data-heba-memory="${index}">${esc(item)}</button>`).join('')}</div><button id="bm-heba-submit" class="bm-btn" type="button" disabled>${t('Submit sequence')}</button></div>`;
    }else{
      activity=`${q.code?`<pre class="bm-heba-code"><code>${esc(q.code)}</code></pre>`:''}<p class="bm-heba-prompt">${esc(q.prompt)}</p><div class="bm-heba-options">${q.choices.map((choice,index)=>`<button class="bm-heba-option" type="button" data-heba-choice="${index}" aria-pressed="false"><span>${String.fromCharCode(65+index)}</span>${esc(choice)}</button>`).join('')}</div><button id="bm-heba-submit" class="bm-btn" type="button" disabled>${t('Submit answer')}</button>`;
    }
    return `<section class="bm-panel bm-heba-exercise">${heading}<div class="bm-heba-progress"><span style="width:${Math.round(exercise.answered*100/exercise.total)}%"></span></div><div class="bm-heba-question-meta"><span>${t('Question')} ${exercise.index+1} ${t('of')} ${exercise.total}</span><span>${esc(q.topic)} · ${esc(q.expected_time_seconds)}s ${t('suggested')}</span></div><div class="bm-heba-content">${resultLine}${activity}<div id="bm-heba-error" class="bm-error" role="alert"></div><p class="bm-note">${t('Correctness is graded and saved on the server. Time is for context, not a penalty.')}</p></div></section>`;
  }
  function cognitiveStimulus(active,t){
    const visual=active.presentation;
    const colors={red:'#EF4444',blue:'#3B82F6',green:'#10B981',yellow:'#F59E0B'};
    const legacyMatch=String(active.stimulus||'').match(/^([A-Z]+) \(shown in (RED|BLUE|GREEN|YELLOW) ink\)$/);
    if(visual?.type==='domain_question'){
      const format=visual.interaction_format;
      if(format==='color_focus'){
        return `<div class="bm-cognitive-stimulus bm-domain-stroop-stage"><span class="bm-stroop-aperture" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 1v3m0 16v3M1 12h3m16 0h3"/></svg></span><div><span class="bm-cognitive-rule">${t('COLOR FOCUS')}</span><strong>${esc(visual.topic)}</strong><small>${t('Scan the question, focus on the concept, then choose your answer.')}</small></div><div class="bm-stroop-sequence" aria-hidden="true"><span>${t('SCAN')}</span><i></i><span>${t('FOCUS')}</span><i></i><span>${t('CHOOSE')}</span></div></div>`;
      }
      if(format==='memory_cards'){
        return `<div class="bm-cognitive-stimulus bm-domain-memory-stage"><span class="bm-memory-deck-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="7" y="3" width="13" height="16" rx="2"/><path d="M4 7v13a1 1 0 0 1 1 1h11M10 8h7m-7 4h7"/></svg></span><div><span class="bm-cognitive-rule">${t('MEMORY DECK')}</span><strong>${esc(visual.topic)}</strong><small>${t('Study each answer card before it flips. Then recall the card with the correct answer.')}</small></div><span class="bm-memory-countdown">${t('CARDS FLIP IN 10 SECONDS')}</span></div>`;
      }
      if(format==='flow_diagram'){
        return `<div class="bm-cognitive-stimulus bm-domain-flow-stage"><div class="bm-flow-start"><span>${t('TOPIC')}</span><strong>${esc(visual.topic)}</strong></div><span class="bm-flow-arrow" aria-hidden="true">→</span><span class="bm-flow-decision" aria-hidden="true">?</span><small>${t('Follow the question to the answer node that fits best.')}</small></div>`;
      }
      if(format==='evidence_chart'){
        return `<div class="bm-cognitive-stimulus bm-domain-evidence-stage"><span class="bm-cognitive-rule">${t('EVIDENCE REVIEW')}</span><div class="bm-evidence-track" aria-hidden="true"><span><i>01</i>${t('CLAIM')}</span><b>→</b><span><i>02</i>${t('CHECK')}</span><b>→</b><span><i>03</i>${t('DECIDE')}</span></div><div class="bm-evidence-topic">${esc(visual.topic)}</div><small>${t('Weigh the evidence before choosing the supported answer.')}</small></div>`;
      }
      if(active.game_id==='progressive_matrices'){
        return `<div class="bm-cognitive-stimulus bm-domain-puzzle"><span class="bm-cognitive-rule"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h6v2a2 2 0 1 0 4 0V4h6v6h-2a2 2 0 1 0 0 4h2v6h-6v-2a2 2 0 1 0-4 0v2H4v-6h2a2 2 0 1 0 0-4H4z"/></svg>${t('COMPLETE THE MISSING PIECE')}</span><div class="bm-domain-puzzle-board"><div class="bm-domain-puzzle-clue"><span class="bm-puzzle-icon bm-puzzle-icon-clue" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M9 18h6M10 22h4M8.2 14.5A7 7 0 1 1 15.8 14.5c-.9.7-1.3 1.4-1.5 2.5h-4.6c-.2-1.1-.6-1.8-1.5-2.5Z"/></svg></span><small>${t('CONCEPT CLUE')}</small><strong>${esc(visual.topic)}</strong></div><span class="bm-domain-puzzle-arrow" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg></span><div class="bm-domain-puzzle-slot" data-puzzle-slot role="status" aria-live="polite"><span class="bm-puzzle-icon bm-puzzle-icon-slot" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 4h6v2a2 2 0 1 0 4 0V4h6v6h-2a2 2 0 1 0 0 4h2v6h-6v-2a2 2 0 1 0-4 0v2H4v-6h2a2 2 0 1 0 0-4H4z"/></svg></span><small>${t('MISSING PIECE')}</small><strong>?</strong></div></div><small class="bm-domain-puzzle-instruction">${t('Select a piece to fill the gap, then submit your answer.')}</small></div>`;
      }
      const modes={
        stroop:['CONCEPT FOCUS','Ignore distractions and choose the accurate subject answer.'],
        n_back:['RECALL & APPLY','Use what you know about this topic to answer the question.'],
        operation_span:['KNOWLEDGE CHECK','Choose the answer that best fits the subject concept.'],
        progressive_matrices:['FIND THE PRINCIPLE','Apply the relevant subject principle to this question.'],
        bayesian_updating:['EVIDENCE CHECK','Select the answer supported by the subject knowledge.'],
      };
      const [label,instruction]=modes[visual.mode]||modes.stroop;
      return `<div class="bm-cognitive-stimulus bm-domain-question-stage"><span class="bm-cognitive-rule">${t(label)}</span><strong>${esc(visual.topic)}</strong><small>${t(instruction)}</small></div>`;
    }
    if(active.game_id==='stroop'&&(visual?.type==='stroop'||legacyMatch)){
      const ink=visual?.ink||legacyMatch?.[2].toLowerCase();
      const word=visual?.word||legacyMatch?.[1];
      return `<div class="bm-cognitive-stimulus bm-cognitive-color-stage"><span class="bm-cognitive-rule">${t('RULE: REPORT INK')}</span><strong style="color:${colors[ink]||'#0f172a'}">${esc(word)}</strong><small>${t('Ignore the letters. Focus on the ink color.')}</small></div>`;
    }
    if(visual.type==='flanker'){
      return `<div class="bm-cognitive-stimulus bm-cognitive-flanker-stage" role="img" aria-label="${t('Five arrows. The center arrow points')} ${esc(visual.target)}.">${visual.arrows.map((arrow,index)=>`<span class="${index===2?'is-target':''}" aria-hidden="true">${esc(arrow)}</span>`).join('')}</div>`;
    }
    if(visual.type==='stop_signal'){
      return `<div id="bm-stop-signal-stage" class="bm-cognitive-stimulus bm-cognitive-signal-stage is-go" aria-live="assertive"><span class="bm-cognitive-rule">${t('RESPOND TO THE ARROW')}</span><strong>${visual.arrow|| (visual.direction==='left'?'←':'→')}</strong><small>${t('A stop cue may appear. Respond quickly, or withhold when it does.')}</small></div>`;
    }
    if(visual.type==='sequence'){
      return `<div class="bm-cognitive-stimulus bm-cognitive-sequence-stage" role="img" aria-label="Three symbols shown in order">${visual.symbols.map((symbol,index)=>`${index?'<span class="bm-cognitive-sequence-arrow" aria-hidden="true">→</span>':''}<strong>${esc(symbol)}</strong>`).join('')}</div>`;
    }
    if(visual.type==='symbol'){
      return `<div class="bm-cognitive-stimulus bm-cognitive-symbol-stage"><strong>${esc(visual.symbol)}</strong></div>`;
    }
    if(visual.type==='task_switch'){
      const rule=visual.rule==='PARITY'?'PARITY':'MAGNITUDE';
      return `<div class="bm-cognitive-stimulus bm-cognitive-rule-stage"><span class="bm-cognitive-rule">${t(rule==='PARITY'?'APPLY RULE: PARITY':'APPLY RULE: MAGNITUDE')}</span><strong>${visual.number}</strong><small>${t(rule==='PARITY'?'Is the number odd or even?':'Is the number low or high?')}</small></div>`;
    }
    if(visual.type==='operation_span'){
      return `<div class="bm-cognitive-stimulus bm-cognitive-operations">${visual.steps.map((step,index)=>`<div class="bm-cognitive-operation" style="--step-index:${index}"><span class="bm-cognitive-operation-index">${String(index+1).padStart(2,'0')}</span><strong>${esc(step.equation)}</strong><span class="bm-cognitive-letter">${t('Remember')} ${esc(step.letter)}</span></div>`).join('')}</div>`;
    }
    if(visual.type==='card_sorting'){
      const glyphs={circle:'●',triangle:'▲',square:'■',star:'★'};
      const colorNames={red:'RED',green:'GREEN',blue:'BLUE',yellow:'YELLOW'};
      const targetGlyph=glyphs[visual.target.shape]||'●';
      return `<div class="bm-cognitive-stimulus bm-cognitive-card-sort"><span class="bm-cognitive-rule">${t('MATCH BY')} ${t(visual.rule)}</span><div class="bm-cognitive-card-row"><div class="bm-cognitive-playing-card is-target"><small>${t('TARGET')}</small><strong style="color:${colors[visual.target.color]||'#0f172a'}">${targetGlyph.repeat(visual.target.count)}</strong><span>${t(colorNames[visual.target.color]||visual.target.color)} · ${t(visual.target.shape)} · ${visual.target.count}</span></div><span class="bm-cognitive-card-arrow" aria-hidden="true">→</span>${visual.cards.map((card,index)=>`<div class="bm-cognitive-playing-card"><small>${t('CARD')} ${index+1}</small><strong style="color:${colors[card.color]||'#0f172a'}">${(glyphs[card.shape]||'●').repeat(card.count)}</strong><span>${t(colorNames[card.color]||card.color)} · ${t(card.shape)} · ${card.count}</span></div>`).join('')}</div></div>`;
    }
    if(visual.type==='matrix'){
      return `<div class="bm-cognitive-stimulus bm-cognitive-matrix" role="img" aria-label="${t('Complete the missing cell in the pattern')}">${visual.rows.flatMap(row=>row.map((cell,index)=>`<span class="${cell===null?'is-missing':''}">${cell===null?'?':cell}</span>`)).join('')}</div>`;
    }
    if(visual.type==='evidence'){
      return `<div class="bm-cognitive-stimulus bm-cognitive-evidence"><span class="bm-cognitive-rule">${t('EVIDENCE PROFILE')}</span><div><span><small>${t('BASE RATE')}</small><strong>${visual.prior}%</strong></span><span><small>${t('SENSITIVITY')}</small><strong>${visual.sensitivity}%</strong></span><span><small>${t('FALSE POSITIVE')}</small><strong>${visual.false_positive}%</strong></span></div><p>${t('A positive test was observed. Estimate the probability the system is truly affected.')}</p></div>`;
    }
    if(visual.type==='composite'){
      const ink=colors[visual.ink]||'#0f172a';
      return `<div class="bm-cognitive-stimulus bm-cognitive-composite-stage"><span class="bm-cognitive-rule">${t('CURRENT INK · IGNORE WORD')}</span><strong style="color:${ink}">${esc(visual.word)}</strong><div><span>${t('Earlier ink')}</span><b style="color:${colors[visual.earlier]||'#0f172a'}">${esc(visual.earlier.toUpperCase())}</b></div></div>`;
    }
    return `<pre class="bm-cognitive-stimulus">${esc(active.stimulus)}</pre>`;
  }
  function learningPath(){mode('learning');const assessment=latest();if(!assessment){root.innerHTML=head('LEARNING PATH','Your plan starts with a baseline.','Complete six questions to get topic-specific next steps.')+`<div class="bm-panel">${button(state.profile?.subject?'/assessment/':'/profile/','Get started')}</div>`;speak('Take a short baseline assessment to shape your learning path.','growth-one','👀 Listening');return;}
    const subjectName=assessment.subject_name||subjects[assessment.subject]||subjects[state.profile?.subject]||'your learning subject';
    root.innerHTML=head('LEARNING PATH','Turn your knowledge into a game.',`${esc(subjectName)} · scored challenges based on your latest assessment.`,button('/assessment/','New challenge'))+`<section class="bm-panel bm-path-loading"><span class="bm-eyebrow">YOUR LEARNING QUEST</span><h2>Preparing your challenges…</h2><p>Your scores save automatically. Pass each activity with 70% to unlock the next.</p><div id="bm-error" class="bm-error" role="alert"></div></section>`;
    task(async()=>renderLearningPath(await api('path',{})));
    speak('Your learning quest is ready to begin. Finish each challenge and score at least 70 to open the next one.','growth-one','🗺️ Quest ready');
  }
  function renderLearningPath(path,result=null){
    if(pathClock!==null){window.clearInterval(pathClock);pathClock=null;}
    const subjectName=assessmentName();
    const t=window.BrainMaxI18n.translate;
    const trialResult=result?.trial_result;
    const pinkyCorrection=trialResult&&!trialResult.correct
      ?`<aside class="bm-pinky-correction" role="status"><strong>${t('Pinky')} · ${t('Feedback for the previous question')}</strong><p class="bm-pinky-question">${esc(trialResult.question_prompt||'')}</p><p>${t('My fault! I should have made that clearer. The correct answer is:')} <b>${esc(trialResult.correct_answer)}</b></p><small>${esc(trialResult.explanation||'')}</small></aside>`
      :'';
    const activityMeta={
      memory:{title:'Sequence memory',short:'MEMORY',icon:'brain',hint:'Study the cards, then rebuild their order from memory.',guidance:'Try saying the cards quietly and grouping them into two pairs before ordering them.'},
      focus:{title:'Focus grid',short:'FOCUS',icon:'target',hint:'Find every square that answers the question. Ignore the distractors.',guidance:'Scan one row at a time. Compare every square with the target, then double-check your selections.'},
      matching:{title:'Concept match',short:'MATCH',icon:'puzzle',hint:'Choose one concept on the left, then choose the explanation that fits it on the right. Match the pairs; there is no speed bonus.',guidance:'Look for the key idea in each explanation, then connect it to the concept in your own words.'},
      balance:{title:'Knowledge tower',short:'KNOWLEDGE',icon:'grid',hint:'Choose the block that best answers the question.',guidance:'Identify the main constraint in the question, then eliminate choices that do not address it.'},
      strategy:{title:'Strategy board',short:'STRATEGY',icon:'book',hint:'Choose the strongest move for this concept.',guidance:'Compare each move with the goal in the question. Eliminate moves that do not advance that goal.'},
    };
    (path.cognitive_catalog||[]).forEach(game=>{
      activityMeta[game.id]={
        title:game.title,
        short:game.id.replaceAll('_',' ').toUpperCase(),
        icon:'brain',
        hint:game.description,
        guidance:game.description,
      };
    });
    const current=path.steps.findIndex(step=>step.id===path.active_step);
    const overall=Math.round(path.steps.filter(step=>step.passed).length*100/path.steps.length);
    const map=path.steps.map((step,index)=>{
      const meta=activityMeta[step.id],status=step.passed?'passed':step.unlocked?'current':'locked';
      const cognitive=(path.cognitive_catalog||[]).some(game=>game.id===step.id);
      const icon=step.passed?pathIcon('check'):pathIcon(meta.icon);
      const recentSeconds=step.seconds?.at(-1);
      const recentAttempt=step.scores?.length?` · Last ${step.scores[step.scores.length-1]}% · ${formatOptionalDuration(recentSeconds)}`:'';
      return `<div class="bm-quest-stop ${status} ${cognitive?'is-cognitive':'is-core'}" ${step.unlocked?'aria-current="step"':''}><span class="bm-quest-node">${icon}<small>${String(index+1).padStart(2,'0')}</small></span><strong>${t(meta.title)}</strong><span class="bm-quest-kind">${t(cognitive?'COGNITIVE GAME':'SUBJECT PRACTICE')}</span><small>${step.passed?`${t('Passed')} · ${step.score}% · ${formatOptionalDuration(recentSeconds)}`:step.unlocked?`${t('Your turn')} · ${esc(step.topic)}${recentAttempt}`:t('Locked until you pass')}</small></div>`;
    }).join('');
    let panel;
    if(path.complete){
      const finalResult=path.final_result;
      const memoryMessage=path.memory_status==='saved'
        ?'Your completed learning results are saved in your private, account-only RAG memory.'
        :path.memory_status==='unavailable'
          ?`Your scores are saved to your account, but RAG memory could not be updated: ${esc(path.memory_error||'Local memory is unavailable.')}`
          :'Turn on Personal history memory in Settings to save completed learning results in your private RAG memory.';
      panel=`<div class="bm-quest-complete"><span class="bm-quest-trophy">${pathIcon('spark')}</span><span class="bm-eyebrow">QUEST COMPLETE</span><h2>${t('All challenges complete!')}</h2>${pinkyCorrection}<p>${t('You passed every activity. Your final score is the average of each passing challenge score; time is reported separately.')}</p><div class="bm-overall-result"><span>FINAL QUEST SCORE</span><strong>${esc(finalResult?.score)}%</strong><small>${finalResult?.attempts} ${t('attempts')} · ${formatOptionalDuration(finalResult?.total_seconds)} ${t('total, including retries')}</small></div><div class="bm-final-scores">${path.steps.map(step=>`<span><strong>${esc(step.score)}%</strong><small>${t(activityMeta[step.id].short)} · ${formatOptionalDuration(step.seconds?.at(-1))}</small></span>`).join('')}</div>${result?.coach?`<div class="bm-coach-feedback"><span>${pathIcon('spark')}</span><div><strong>${t('Brain’s feedback and next step')}</strong><p>${esc(result.coach)}</p></div></div>`:''}<p class="bm-memory-status" role="status">${memoryMessage}${path.memory_status==='disabled'?` <a href="/settings/">${t('Open Settings')}</a>.`:''}</p><a class="bm-btn secondary" href="/assessment/">${t('Practice with a new assessment')} →</a></div>`;
      const weakestActivity=path.steps.reduce(
        (weakest,step)=>step.score<weakest.score?step:weakest,
        path.steps[0],
      );
      const finalStatus=finalResult.score>=80
        ?t('Strong progress')
        :finalResult.score>=50
          ?t('Building progress')
          :t('Keep practicing');
      const finalStatusPanel=`<section class="bm-final-status"><div><span class="bm-eyebrow">${t('FINAL LEARNING STATUS')}</span><strong>${finalStatus}</strong><small>${t('Based on your final learning-path score.')}</small></div><div><span class="bm-note">${t('Suggested activity to revisit')}</span><strong>${t(activityMeta[weakestActivity.id].title)}</strong><small>${esc(weakestActivity.score)}% ${t('on this activity')}</small></div></section>`;
      panel=panel.replace(
        '</div><div class="bm-final-scores">',
        `</div>${finalStatusPanel}<div class="bm-final-scores">`,
      );
    }else{
      const challenge=path.challenge,meta=activityMeta[path.active_step];
      const currentProgress=path.steps[current];
      const resultProgress=result?path.steps.find(step=>step.id===result.step):null;
      const lastFeedback=(resultProgress||currentProgress).feedback?.at(-1);
      const attemptResult=result?.result||(
        currentProgress.score===null?null:{
          score:currentProgress.score,
          seconds:currentProgress.seconds?.at(-1),
          passed:false,
          attempts:currentProgress.attempts,
        }
      );
      let game='';
      if(challenge.content_exhausted){
        game=`<div class="bm-content-exhausted" role="alert"><strong>${t('No fresh questions remain for this activity.')}</strong><p>${t('Pinky will not repeat questions you have already seen. A new reviewed question set is needed before you can retry this activity.')}</p><a class="bm-btn secondary" href="/assessment/">${t('Start a new assessment')} →</a></div>`;
      }else if(challenge.id==='memory'){
        game=`<p class="bm-card-prompt">${esc(challenge.prompt)}</p><p class="bm-game-instruction">${meta.hint}</p><div class="bm-memory-sequence" aria-label="Remember these cards in order">${challenge.cards.map((card,index)=>`<span class="bm-memory-card"><small>${index+1}</small>${esc(card)}</span>`).join('')}</div><button id="bm-memory-start" class="bm-btn" type="button">Hide cards & recall →</button><div id="bm-memory-recall" class="bm-memory-recall" hidden><p><strong>Your sequence</strong> <span id="bm-memory-count">0 / 4 cards</span></p><ol id="bm-memory-answer"></ol><div class="bm-memory-options">${challenge.options.map((option,index)=>`<button class="bm-memory-option" type="button" data-memory-option="${index}">${esc(option)}</button>`).join('')}</div><div class="bm-game-actions"><button id="bm-memory-reset" class="bm-btn secondary" type="button">Start over</button><button id="bm-memory-submit" class="bm-btn" type="button" disabled>Score my memory</button></div></div>`;
      }else if(challenge.id==='focus'){
        game=`<p class="bm-card-prompt">${esc(challenge.prompt)}</p><p class="bm-game-instruction">${meta.hint}</p><div class="bm-signal-grid" role="group" aria-label="Select all matching answers">${challenge.cells.map((cell,index)=>`<button class="bm-signal-cell" type="button" data-focus-cell="${index}" aria-pressed="false"><span>${index+1}</span>${esc(cell)}</button>`).join('')}</div><p id="bm-focus-count" class="bm-note" aria-live="polite">0 squares selected</p><div class="bm-game-actions"><button id="bm-focus-submit" class="bm-btn" type="button">Score my focus</button></div>`;
      }else if(challenge.id==='matching'){
        game=`<p class="bm-game-instruction">${meta.hint}</p><div class="bm-match-board"><section class="bm-match-column" aria-label="Concepts">${challenge.left_cards.map((card,index)=>`<button class="bm-match-card concept" type="button" data-match-left="${index}" aria-pressed="false"><span>${String(index+1).padStart(2,'0')}</span>${esc(card)}</button>`).join('')}</section><section class="bm-match-column" aria-label="Explanations">${challenge.right_cards.map((card,index)=>`<button class="bm-match-card explanation" type="button" data-match-right="${index}" aria-pressed="false"><span>${String.fromCharCode(65+index)}</span>${esc(card)}</button>`).join('')}</section></div><p id="bm-match-status" class="bm-note" aria-live="polite"></p><div class="bm-game-actions"><button id="bm-match-submit" class="bm-btn" type="button" disabled>Score my matches</button></div>`;
      }else if(challenge.game_id&&!challenge.content_exhausted){
        const trialFeedback=trialResult?.correct
          ?`<p class="bm-cognitive-round-feedback is-correct" role="status">${t('Correct response')} · ${t('Trial')} ${trialResult.index} ${t('of')} ${trialResult.total}</p>`
          :'';
        const progress=Math.round(challenge.trial_index*100/challenge.trial_total);
        const heading=`<div class="bm-cognitive-round-heading"><span class="bm-eyebrow">${t(challenge.title)}</span><strong>${t('Trial')} ${challenge.trial_index+1} ${t('of')} ${challenge.trial_total}</strong></div><div class="bm-cognitive-round-progress" role="progressbar" aria-label="${t('Game trial progress')}" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100"><span style="width:${progress}%"></span></div>${trialFeedback}<p class="bm-card-prompt">${esc(challenge.prompt)}</p>`;
        if(challenge.game_id==='operation_span'&&challenge.presentation?.type==='operation_span'){
          game=`<div class="bm-cognitive-round">${heading}<div id="bm-operation-span-stage" class="bm-operation-span-stage"></div></div>`;
        }else{
          const colors={RED:'#EF4444',BLUE:'#3B82F6',GREEN:'#10B981',YELLOW:'#F59E0B'};
          const puzzleMode=challenge.game_id==='progressive_matrices'&&challenge.presentation?.type==='domain_question';
          const format=challenge.presentation?.interaction_format;
          const memoryMode=format==='memory_cards';
          const flowMode=format==='flow_diagram';
          const colorMode=format==='color_focus';
          const evidenceMode=format==='evidence_chart';
          const options=challenge.options.map((option,index)=>{
            const color=colors[String(option).toUpperCase()];
            const letter=String.fromCharCode(65+index);
            if(memoryMode){
              return `<button class="bm-cognitive-option bm-memory-answer-card" type="button" data-cognitive-path-answer="${index}" data-memory-card="${index}" aria-label="${t('Answer card')} ${index+1}" aria-pressed="false" disabled><span class="bm-memory-card-inner"><span class="bm-memory-card-face bm-memory-card-front">${esc(option)}</span><span class="bm-memory-card-face bm-memory-card-back" aria-hidden="true">${letter}</span></span></button>`;
            }
            if(flowMode){
              return `<button class="bm-cognitive-option bm-flow-node" type="button" data-cognitive-path-answer="${index}" aria-pressed="false"><span class="bm-flow-node-number">${letter}</span><span>${esc(option)}</span></button>`;
            }
            if(colorMode){
              return `<button class="bm-cognitive-option bm-color-choice" type="button" data-cognitive-path-answer="${index}" aria-pressed="false"><span class="bm-color-choice-mark" aria-hidden="true">${letter}</span><span>${esc(option)}</span></button>`;
            }
            if(evidenceMode){
              return `<button class="bm-cognitive-option bm-evidence-choice" type="button" data-cognitive-path-answer="${index}" aria-pressed="false"><span class="bm-evidence-choice-label">${letter}</span><span>${esc(option)}</span></button>`;
            }
            return `<button class="bm-cognitive-option${puzzleMode?' bm-puzzle-piece':''}" type="button" data-cognitive-path-answer="${index}"${puzzleMode?` data-puzzle-label="${esc(option)}" style="--piece-index:${index}"`:''} aria-pressed="false">${puzzleMode?'<svg class="bm-puzzle-piece-mark" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h6v2a2 2 0 1 0 4 0V4h6v6h-2a2 2 0 1 0 0 4h2v6h-6v-2a2 2 0 1 0-4 0v2H4v-6h2a2 2 0 1 0 0-4H4z"/></svg>':''}${color?`<span class="bm-cognitive-option-color" style="--option-color:${color}" aria-hidden="true"></span>`:''}<span class="bm-puzzle-piece-label">${esc(option)}</span></button>`;
          }).join('');
          const optionsClass=puzzleMode?'bm-puzzle-pieces':memoryMode?'bm-memory-cards':flowMode?'bm-flow-options':colorMode?'bm-color-options':evidenceMode?'bm-evidence-options':'';
          const optionsLabel=puzzleMode?'Choose a puzzle piece':memoryMode?'Choose the remembered answer card':flowMode?'Choose an answer node':'Choose your response';
          game=`<div class="bm-cognitive-round">${heading}${cognitiveStimulus(challenge,t)}<div class="bm-cognitive-options ${optionsClass}" role="group" aria-label="${t(optionsLabel)}">${options}</div>${memoryMode?`<p id="bm-memory-card-status" class="bm-memory-card-status" role="status" aria-live="polite">${t('Study the answer cards. They will flip in three seconds.')}</p>`:''}<div class="bm-game-actions"><button id="bm-cognitive-path-submit" class="bm-btn" type="button" disabled>${t('Submit response')}</button></div></div>`;
        }
      }else{
        const options=challenge.id==='balance'?challenge.blocks:challenge.moves;
        game=`<p class="bm-card-prompt">${esc(challenge.prompt)}</p><p class="bm-game-instruction">${meta.hint}</p><div class="${challenge.id==='balance'?'bm-tower-board':'bm-strategy-board'}" role="group" aria-label="Choose your answer">${options.map((option,index)=>`<button class="${challenge.id==='balance'?'bm-tower-block':'bm-strategy-move'}" type="button" data-answer="${index}" aria-pressed="false">${challenge.id==='strategy'?`<span class="bm-chess-square" aria-hidden="true">${['♘','♗','♖','♕'][index]}</span>`:`<span class="bm-block-number">${index+1}</span>`}<span>${esc(option)}</span></button>`).join('')}</div><p id="bm-answer-status" class="bm-note" aria-live="polite">Choose one answer to continue.</p><div class="bm-game-actions"><button id="bm-answer-submit" class="bm-btn" type="button" disabled>Score my ${challenge.id==='balance'?'move':'strategy'}</button></div>`;
      }
      const feedback=attemptResult?`<div class="bm-quest-feedback ${attemptResult.passed?'is-passed':'try-again'}" role="status"><div><strong>${attemptResult.passed?t('Challenge passed!'):t('Not quite — give it another try.')}</strong><p>${t('You scored')} ${attemptResult.score}% ${t('in')} ${formatOptionalDuration(attemptResult.seconds)}. ${attemptResult.passed?`${t('That’s above the pass mark. The next step is unlocked.')} ${esc(attemptResult.explanation||'')}`:`${t('You need')} ${path.pass_score}% ${t('to unlock the next challenge. Your attempt is saved.')}`}</p>${resultProgress?.scores?.length>1?`<small>${t('Attempt scores')}: ${resultProgress.scores.map((score,index)=>`${score}% · ${formatOptionalDuration(resultProgress.seconds[index])}`).join(' · ')}</small>`:''}</div><span class="bm-feedback-score">${attemptResult.score}%</span></div>`:'';
      const attemptNumber=currentProgress.attempts+1;
      const coach=result?.coach?{text:result.coach,source:result.coach_source,error:result.coach_error}:lastFeedback;
      const pinkySupport=attemptResult&&!attemptResult.passed?`<p class="bm-pinky-support">${t('Pinky says: A missed step is a clue, not a setback. Read Brain’s tip and try again when you’re ready.')}</p>`:'';
      const coachFeedback=coach?`<div class="bm-coach-feedback"><span>${pathIcon('spark')}</span><div><strong>${esc(coach.source||'Brain’s feedback and next step')}</strong><p>${esc(coach.text)}</p>${coach.error?`<small>AI feedback unavailable: ${esc(coach.error)}</small>`:''}${pinkySupport}</div></div>`:'';
      panel=`<div class="bm-quest-card"><div class="bm-quest-card-heading"><span class="bm-step-icon">${pathIcon(meta.icon)}</span><div><span class="bm-eyebrow">${t('CHALLENGE')} ${String(current+1).padStart(2,'0')} · ${t(meta.short)}</span><h2>${t(meta.title)}</h2><p>${esc(challenge.topic)} · ${t('Attempt')} ${attemptNumber}</p></div><span class="bm-quest-clock" aria-label="Time spent on this attempt"><span aria-hidden="true">◷</span><strong id="bm-quest-clock">${formatDuration(challenge.elapsed_seconds||0)}</strong><small>${t('time')}</small></span></div><div class="bm-quest-progress" role="progressbar" aria-label="Learning quest progress" aria-valuenow="${overall}" aria-valuemin="0" aria-valuemax="100"><span style="width:${overall}%"></span></div><div class="bm-game-stage ${challenge.id}">${game}</div>${pinkyCorrection}${feedback}${coachFeedback}<div id="bm-error" class="bm-error" role="alert"></div><p class="bm-pass-note">${t('Passing score')}: <strong>${path.pass_score}%</strong> · ${t('Time is recorded for context, not included in your score. Retry as often as you need.')}</p></div>`;
    }
    const routeProgress=path.complete?100:Math.max(0,current*100/Math.max(1,path.steps.length-1));
    const recommendation=path.recommendation||{activity_type:'balanced'};
    const recommendedStep=activityMeta[recommendation.recommended_step];
    const recommendationTitle={
      cognitive:t('Cognitive practice'),
      pedagogical:t('Subject learning practice'),
      balanced:t('Balanced practice'),
    }[recommendation.activity_type]||t('Balanced practice');
    const interventionReview=recommendation.intervention_review;
    const reviewOutcome=interventionReview?.status==='reviewed'
      ?`${t('Follow-up after three sessions')}: ${t(interventionReview.outcome==='improved'?'Practice scores improved':interventionReview.outcome==='declined'?'Practice scores dipped':interventionReview.outcome==='engagement_improved'?'Engagement improved':interventionReview.outcome==='engagement_declined'?'Engagement declined':'No clear score change')} (${interventionReview.score_change>0?'+':''}${interventionReview.score_change} ${t('points')}).`
      :'';
    const adaptationMessage=recommendation.plateau?.intervention_required
      ?esc(recommendation.plateau.message)
      :'';
    const learnerRequested=recommendation.decision_basis==='learner_requested_adaptation';
    const requestedAdjustments=recommendation.coach_adjustments||{};
    const requestedMessages=[];
    if(requestedAdjustments.focus_topic){
      requestedMessages.push(`${t('You asked to focus practice questions on')} ${esc(t(requestedAdjustments.focus_topic))}.`);
    }
    if(requestedAdjustments.activity_type==='cognitive'){
      requestedMessages.push(`${t('You asked for more game-based practice; this path starts with')} ${t(recommendedStep?.title||'a game activity')}.`);
    }else if(requestedAdjustments.activity_type==='pedagogical'){
      requestedMessages.push(`${t('You asked for more subject practice; this path starts with')} ${t(recommendedStep?.title||'a practice activity')}.`);
    }
    const requestedFormatMessage=requestedMessages.join(' ')
      ||(recommendation.activity_type==='cognitive'
        ?`${t('You asked for more game-based practice; this path starts with')} ${t(recommendedStep?.title||'a game activity')}.`
        :`${t('You asked for more subject practice; this path starts with')} ${t(recommendedStep?.title||'a practice activity')}.`);
    const defaultRecommendationReason=recommendation.activity_type==='balanced'
      ?t('Complete this path to create an initial practice sample. Future recommendations compare cognitive-game and subject-learning results.')
      :`${t('Based on recent completed-path practice averages')}: ${t('Cognitive practice')} ${recommendation.cognitive_score}% (${recommendation.cognitive_attempts} ${t('attempts')}) · ${t('Subject learning practice')} ${recommendation.pedagogical_score}% (${recommendation.pedagogical_attempts} ${t('attempts')}). ${t('This guides practice only; it is not a measure of ability.')}`;
    const recommendationReason=[reviewOutcome,adaptationMessage].filter(Boolean).join(' ')
      ||(learnerRequested?requestedFormatMessage:defaultRecommendationReason);
    const recommendationPanel=`<section class="bm-panel bm-adaptive-recommendation"><span class="bm-recommendation-icon">${pathIcon(recommendation.activity_type==='cognitive'?'brain':recommendation.activity_type==='pedagogical'?'book':'spark')}</span><div class="bm-recommendation-copy"><span class="bm-eyebrow">${t('ADAPTIVE RECOMMENDATION')}</span><h2>${recommendationTitle}${recommendedStep?` · ${t(recommendedStep.title)}`:''}</h2><p>${recommendationReason}</p></div><span class="bm-recommendation-tag">${t(learnerRequested?'Your requested adjustment':recommendation.plateau?.intervention_required?'Adaptive adjustment':recommendation.activity_type==='balanced'?'Getting started':'Based on your practice')}</span></section>`;
    const baselinePanel=pathBaselinePanel(path.baseline,t);
    const exercisePanel=hebaExercisePanel(path.heba_exercise,result,t);
    const questMap=`<section class="bm-panel bm-quest-map"><div class="bm-quest-map-heading"><div><span class="bm-eyebrow">${esc(subjectName)} · ${t('NUMBERED PRACTICE PLAN')}</span><h2>${path.complete?t('All challenges complete!'):t('Your activities, in order')}</h2><p>${t('Your ten activities combine subject practice with five cognitive games that use concepts from your selected domain.')}</p><p class="bm-quest-map-summary">${path.steps.length} ${t('activities total')} · ${path.cognitive_catalog.length} ${t('cognitive game exercises')} · ${t(path.pass_score+'% required to pass each activity')}</p><button id="bm-path-restart" class="bm-btn secondary" type="button">${t('Restart path with fresh questions')}</button></div><div class="bm-quest-total"><strong>${path.steps.filter(step=>step.passed).length}<small> / ${path.steps.length}</small></strong><span>${t('passed')}</span></div></div><nav class="bm-quest-route" style="--quest-progress:${routeProgress}%" aria-label="${esc(subjectName)} numbered practice activities">${map}</nav></section>`;
    const dimensionPanel=dimensionRadarPanel(path,t);
    root.innerHTML=head('LEARNING PATH',`${esc(subjectName)} learning path`,t('Complete each numbered activity in order. Your subject exercises and cognitive games share one progress path.'),button('/assessment/','New challenge'))+baselinePanel+dimensionPanel+questMap+`<section class="bm-panel bm-quest-work">${panel}</section>`+collaborationPanel(path.collaboration,t)+recommendationPanel+exercisePanel+`<p class="bm-note bm-evidence-note">${t('These short activities are practice games, not a validated cognitive or ability measure. Scores show only how you did on this set.')}</p>`;
    root.querySelector('#bm-path-restart').onclick=()=>{
      if(!window.confirm(t('Restart all ten activities? Current scores and progress will be saved in history.')))return;
      task(async()=>renderLearningPath(await api('path',{action:'restart'})));
    };
    wireCollaborationPanel(t);
    if(path.heba_exercise&&!path.heba_exercise.completed)wireHebaExercise(path);
    if(path.complete||path.challenge?.content_exhausted){
      if(result?.coach)speak(result.coach,'growth-three','🧠 Brain’s feedback',[],'brain');
      return;
    }
    const clockStarted=Date.now(),clockOffset=path.challenge.elapsed_seconds||0;
    pathClock=window.setInterval(()=>{const clock=root.querySelector('#bm-quest-clock');if(clock)clock.textContent=formatDuration(clockOffset+Math.floor((Date.now()-clockStarted)/1000));},1000);
    if(path.active_step==='memory')wireMemoryGame(path);
    else if(path.active_step==='focus')wireFocusGame(path);
    else if(path.active_step==='matching')wireMatchingGame(path);
    else if(path.challenge.game_id==='operation_span'&&path.challenge.presentation?.type==='operation_span')wireOperationSpan(path,t);
    else if(path.challenge.game_id)wireCognitivePathGame(path);
    else wireChoiceGame(path);
    if(result?.trial_result&&!result.trial_result.correct){
      const correction=result.trial_result;
      const spokenCorrection=`${t('About the previous question')}: ${correction.question_prompt||''} ${t('My fault! I should have made that clearer. The correct answer is:')} ${correction.correct_answer||''}. ${correction.explanation||''}`;
      speak(spokenCorrection,'speaking',t('Pinky explains'),[],'pinky');
    }else if(result?.result){
      const support={
        en:'That’s okay—each try helps. Read Brain’s tip above and try again when you’re ready.',
        fr:'Ce n’est pas grave ; chaque essai aide. Lisez le conseil de Brain ci-dessus et réessayez quand vous le souhaitez.',
        ar:'لا بأس، فكل محاولة تساعدك. اقرأ نصيحة Brain أعلاه ثم حاول مجدداً عندما تكون مستعداً.',
      };
      if(result.result.passed)speak(result.coach||t('Challenge passed!'),'growth-three','🧠 Brain’s feedback',[],'brain');
      else speak(t(support[state.preferences?.language||'en']),'neutral','💗 Pinky is here',[],'pinky');
    }else if(recommendation.plateau?.intervention_required){
      speak(recommendation.plateau.message,'neutral','💗 Pinky suggests a fresh approach',[],'pinky');
    }else if(!result){
      const meta=activityMeta[path.active_step];
      speak(`${t(meta.title)}. ${t(meta.guidance)}`,'neutral','🧠 Brain’s tip',['Explain this step']);
    }
  }
  function formatDuration(seconds){
    const total=Math.max(0,Number(seconds)||0);
    const minutes=Math.floor(total/60),remaining=total%60;
    return `${minutes}:${String(remaining).padStart(2,'0')}`;
  }
  function formatOptionalDuration(seconds){return Number.isFinite(seconds)?formatDuration(seconds):'Time not recorded';}
  function assessmentName(){
    const assessment=latest();
    return assessment?.subject_name||subjects[assessment?.subject]||subjects[state.profile?.subject]||'Your learning subject';
  }
  function wireHebaExercise(path){
    const exercise=path.heba_exercise,question=exercise.question;
    const submit=answer=>task(async()=>{
      const response=await api('path',{
        step:'heba_exercise',
        index:exercise.index,
        answer,
      });
      renderLearningPath(response,response);
    });
    if(question.exercise_type==='memory'){
      const sequence=[];
      const recall=root.querySelector('#bm-heba-recall');
      const update=()=>{
        root.querySelector('#bm-heba-memory-count').textContent=`${sequence.length} / ${question.choices.length}`;
        root.querySelector('#bm-heba-memory-answer').innerHTML=sequence.map((index,position)=>`<li>${esc(question.choices[index])}<button type="button" data-heba-remove="${position}" aria-label="Remove ${esc(question.choices[index])}">×</button></li>`).join('');
        root.querySelectorAll('[data-heba-memory]').forEach(button=>{button.disabled=sequence.includes(Number(button.dataset.hebaMemory));});
        root.querySelector('#bm-heba-submit').disabled=sequence.length!==question.choices.length;
        root.querySelectorAll('[data-heba-remove]').forEach(button=>button.onclick=()=>{sequence.splice(Number(button.dataset.hebaRemove),1);update();});
      };
      root.querySelector('#bm-heba-memory-start').onclick=()=>{
        recall.hidden=false;
        root.querySelector('#bm-heba-sequence').hidden=true;
        root.querySelector('#bm-heba-memory-start').hidden=true;
      };
      root.querySelectorAll('[data-heba-memory]').forEach(button=>button.onclick=()=>{
        if(sequence.length<question.choices.length){sequence.push(Number(button.dataset.hebaMemory));update();}
      });
      root.querySelector('#bm-heba-submit').onclick=()=>submit(sequence);
      update();
      return;
    }
    let selected=null;
    root.querySelectorAll('[data-heba-choice]').forEach(button=>button.onclick=()=>{
      selected=Number(button.dataset.hebaChoice);
      root.querySelectorAll('[data-heba-choice]').forEach(option=>{
        const active=option===button;
        option.classList.toggle('selected',active);
        option.setAttribute('aria-pressed',String(active));
      });
      root.querySelector('#bm-heba-submit').disabled=false;
    });
    root.querySelector('#bm-heba-submit').onclick=()=>{if(selected!==null)submit(selected);};
  }
  function submitPathAttempt(path,answers){
    if(pathClock!==null){window.clearInterval(pathClock);pathClock=null;}
    task(async()=>{
      const response=await api('path',{step:path.active_step,answers});
      renderLearningPath(response,response);
    });
  }
  function wireMemoryGame(path){
    const challenge=path.challenge,recall=root.querySelector('#bm-memory-recall'),sequence=[];
    root.querySelector('#bm-memory-start').onclick=()=>{recall.hidden=false;root.querySelector('.bm-memory-sequence').hidden=true;root.querySelector('#bm-memory-start').hidden=true;};
    const update=()=>{
      root.querySelector('#bm-memory-count').textContent=`${sequence.length} / 4 cards`;
      root.querySelector('#bm-memory-answer').innerHTML=sequence.map((index,position)=>`<li>${esc(challenge.options[index])}<button type="button" data-remove-memory="${position}" aria-label="Remove ${esc(challenge.options[index])}">×</button></li>`).join('');
      root.querySelectorAll('[data-memory-option]').forEach(button=>{button.disabled=sequence.includes(Number(button.dataset.memoryOption));});
      root.querySelector('#bm-memory-submit').disabled=sequence.length!==4;
      root.querySelectorAll('[data-remove-memory]').forEach(button=>button.onclick=()=>{sequence.splice(Number(button.dataset.removeMemory),1);update();});
    };
    root.querySelectorAll('[data-memory-option]').forEach(button=>button.onclick=()=>{if(sequence.length<4){sequence.push(Number(button.dataset.memoryOption));update();}});
    root.querySelector('#bm-memory-reset').onclick=()=>{sequence.splice(0);recall.hidden=true;root.querySelector('.bm-memory-sequence').hidden=false;root.querySelector('#bm-memory-start').hidden=false;update();};
    root.querySelector('#bm-memory-submit').onclick=()=>submitPathAttempt(path,sequence);
    update();
  }
  function wireFocusGame(path){
    const selected=new Set(),count=root.querySelector('#bm-focus-count');
    root.querySelectorAll('[data-focus-cell]').forEach(button=>button.onclick=()=>{
      const cell=Number(button.dataset.focusCell);
      if(selected.has(cell)){selected.delete(cell);button.setAttribute('aria-pressed','false');button.classList.remove('selected');}
      else{selected.add(cell);button.setAttribute('aria-pressed','true');button.classList.add('selected');}
      count.textContent=`${selected.size} square${selected.size===1?'':'s'} selected`;
    });
    root.querySelector('#bm-focus-submit').onclick=()=>submitPathAttempt(path,[...selected]);
  }
  function wireMatchingGame(path){
    const matches=new Map();
    let selectedLeft=null;
    const submit=root.querySelector('#bm-match-submit');
    const status=root.querySelector('#bm-match-status');
    const leftButtons=[...root.querySelectorAll('[data-match-left]')];
    const rightButtons=[...root.querySelectorAll('[data-match-right]')];
    const total=leftButtons.length;
    const required=Math.ceil(total*path.pass_score/100);
    const refresh=()=>{
      leftButtons.forEach(button=>{
        const index=Number(button.dataset.matchLeft);
        const matched=matches.has(index);
        button.classList.toggle('matched',matched);
        button.setAttribute('aria-pressed',String(selectedLeft===index));
      });
      rightButtons.forEach(button=>{
        const index=Number(button.dataset.matchRight);
        const matched=[...matches.values()].includes(index);
        button.classList.toggle('matched',matched);
        button.setAttribute('aria-pressed',String(matched));
      });
      const instruction=selectedLeft===null
        ?'Choose a concept, then its matching explanation.'
        :'Now choose the matching explanation.';
      status.textContent=`${window.BrainMaxI18n.translate(instruction)} ${matches.size} ${window.BrainMaxI18n.translate('of')} ${total} ${window.BrainMaxI18n.translate('pairs matched;')} ${required} ${window.BrainMaxI18n.translate('needed to pass.')}`;
      submit.disabled=matches.size<required;
    };
    leftButtons.forEach(button=>button.onclick=()=>{
      selectedLeft=Number(button.dataset.matchLeft);
      refresh();
    });
    rightButtons.forEach(button=>button.onclick=()=>{
      if(selectedLeft===null){
        status.textContent='Choose a concept on the left first.';
        return;
      }
      const right=Number(button.dataset.matchRight);
      for(const [left,matchedRight] of matches){
        if(matchedRight===right)matches.delete(left);
      }
      matches.set(selectedLeft,right);
      selectedLeft=null;
      refresh();
    });
    refresh();
    submit.onclick=()=>submitPathAttempt(
      path,
      [...matches.entries()].sort((first,second)=>first[0]-second[0]),
    );
  }
  function wireChoiceGame(path){
    let selected=null;
    const submit=root.querySelector('#bm-answer-submit');
    root.querySelectorAll('[data-answer]').forEach(button=>button.onclick=()=>{
      selected=Number(button.dataset.answer);
      root.querySelectorAll('[data-answer]').forEach(option=>{
        const active=option===button;
        option.classList.toggle('selected',active);
        option.setAttribute('aria-pressed',String(active));
      });
      root.querySelector('#bm-answer-status').textContent='Answer selected. Score your move when you are ready.';
      submit.disabled=false;
    });
    submit.onclick=()=>{if(selected!==null)submitPathAttempt(path,[selected]);};
  }
  function wireCognitivePathGame(path){
    let selected=null;
    const submit=root.querySelector('#bm-cognitive-path-submit');
    let responseTimer=null,stopTimer=null,memoryTimer=null,submitting=false;
    const visual=path.challenge.presentation;
    const sendAnswer=answer=>{
      if(submitting)return;
      submitting=true;
      window.clearTimeout(responseTimer);
      window.clearTimeout(stopTimer);
      window.clearTimeout(memoryTimer);
      if(pathClock!==null){window.clearInterval(pathClock);pathClock=null;}
      task(async()=>{
        try{
          const response=await api('path',{
            step:path.active_step,
            answer,
          });
          renderLearningPath(response,response);
        }catch(error){
          submitting=false;
          throw error;
        }
      });
    };
    root.querySelectorAll('[data-cognitive-path-answer]').forEach(button=>button.onclick=()=>{
      selected=Number(button.dataset.cognitivePathAnswer);
      const puzzleSlot=root.querySelector('[data-puzzle-slot]');
      if(puzzleSlot){
        puzzleSlot.classList.add('is-filled');
        puzzleSlot.querySelector('strong').textContent=button.dataset.puzzleLabel;
      }
      root.querySelectorAll('[data-cognitive-path-answer]').forEach(option=>{
        const active=option===button;
        option.classList.toggle('selected',active);
        option.setAttribute('aria-pressed',String(active));
      });
      submit.disabled=false;
      if(path.challenge.game_id==='stop_signal')sendAnswer(selected);
    });
    if(visual?.interaction_format==='memory_cards'){
      const cards=[...root.querySelectorAll('[data-memory-card]')];
      const status=root.querySelector('#bm-memory-card-status');
      memoryTimer=window.setTimeout(()=>{
        cards.forEach((card,index)=>{
          card.disabled=false;
          card.classList.add('is-flipped');
          card.setAttribute('aria-label',`${window.BrainMaxI18n.translate('Memory card')} ${index+1}`);
        });
        if(status)status.textContent=window.BrainMaxI18n.translate('Choose the card that held the correct answer.');
      },10000);
    }
    if(path.challenge.game_id==='stop_signal'){
      const stage=root.querySelector('#bm-stop-signal-stage');
      if(visual.is_stop){
        stopTimer=window.setTimeout(()=>{
          stage.classList.remove('is-go');
          stage.classList.add('is-stop');
          stage.querySelector('.bm-cognitive-rule').textContent='STOP SIGNAL · WITHHOLD';
          stage.querySelector('small').textContent='Do not respond. Wait for this trial to end.';
          responseTimer=window.setTimeout(()=>sendAnswer(null),900);
        },visual.ssd_ms);
      }else{
        responseTimer=window.setTimeout(()=>sendAnswer(null),2500);
      }
      return;
    }
    submit.onclick=()=>{if(selected!==null)sendAnswer(selected);};
  }
  function wireOperationSpan(path,t){
    const challenge=path.challenge;
    const steps=challenge.presentation.steps;
    const stage=root.querySelector('#bm-operation-span-stage');
    const mathAnswers=[];
    const recalledLetters=[];
    let stepIndex=0,selectedMath=null;

    const showMath=()=>{
      selectedMath=null;
      const step=steps[stepIndex];
      stage.innerHTML=`<span class="bm-operation-span-label">${t('MATH CHECK')} · ${stepIndex+1}/${steps.length}</span><strong class="bm-operation-span-equation">${esc(step.equation)}</strong><div class="bm-cognitive-options"><button class="bm-cognitive-option" type="button" data-equation-answer="true">${t('TRUE')}</button><button class="bm-cognitive-option" type="button" data-equation-answer="false">${t('FALSE')}</button></div><button class="bm-btn" type="button" id="bm-operation-span-check" disabled>${t('Check equation')}</button>`;
      stage.querySelectorAll('[data-equation-answer]').forEach(button=>button.onclick=()=>{
        selectedMath=button.dataset.equationAnswer==='true';
        stage.querySelectorAll('[data-equation-answer]').forEach(option=>{
          const active=option===button;
          option.classList.toggle('selected',active);
          option.setAttribute('aria-pressed',String(active));
        });
        stage.querySelector('#bm-operation-span-check').disabled=false;
      });
      stage.querySelector('#bm-operation-span-check').onclick=()=>{
        if(selectedMath===null)return;
        mathAnswers.push(selectedMath);
        showLetter();
      };
    };
    const showLetter=()=>{
      stage.innerHTML=`<span class="bm-operation-span-label">${t('REMEMBER THIS CONCEPT')}</span><strong class="bm-operation-span-letter">${esc(steps[stepIndex].letter)}</strong><button class="bm-btn" type="button" id="bm-operation-span-next">${t(stepIndex+1<steps.length?'Next equation':'Recall the sequence')} →</button>`;
      stage.querySelector('#bm-operation-span-next').onclick=()=>{
        stepIndex++;
        if(stepIndex<steps.length)showMath();
        else showRecall();
      };
    };
    const showRecall=()=>{
      const update=()=>{
        stage.querySelector('#bm-operation-span-sequence').innerHTML=recalledLetters.map((letter,index)=>`<li>${esc(letter)}<button type="button" data-remove-letter="${index}" aria-label="${t('Remove')} ${esc(letter)}">×</button></li>`).join('');
        stage.querySelectorAll('[data-recall-letter]').forEach(button=>{
          button.disabled=recalledLetters.includes(button.dataset.recallLetter);
        });
        stage.querySelector('#bm-operation-span-submit').disabled=recalledLetters.length!==steps.length;
        stage.querySelectorAll('[data-remove-letter]').forEach(button=>button.onclick=()=>{
          recalledLetters.splice(Number(button.dataset.removeLetter),1);
          update();
        });
      };
      stage.innerHTML=`<span class="bm-operation-span-label">${t('RECALL THE CONCEPTS IN ORDER')}</span><ol id="bm-operation-span-sequence" class="bm-operation-span-sequence"></ol><div class="bm-cognitive-options">${challenge.options.map(letter=>`<button class="bm-cognitive-option" type="button" data-recall-letter="${esc(letter)}">${esc(letter)}</button>`).join('')}</div><button class="bm-btn" type="button" id="bm-operation-span-submit" disabled>${t('Submit sequence')}</button>`;
      stage.querySelectorAll('[data-recall-letter]').forEach(button=>button.onclick=()=>{
        if(recalledLetters.length<steps.length){
          recalledLetters.push(button.dataset.recallLetter);
          update();
        }
      });
      stage.querySelector('#bm-operation-span-submit').onclick=()=>task(async()=>{
        if(recalledLetters.length!==steps.length)return;
        if(pathClock!==null){window.clearInterval(pathClock);pathClock=null;}
        const response=await api('path',{
          step:path.active_step,
          answer:{
            math_answers:mathAnswers,
            recalled_letters:recalledLetters,
          },
        });
        renderLearningPath(response,response);
      });
      update();
    };
    showMath();
  }
  function twinMemoryHero(assessment=null){
    const profile=state.profile||{},goal=state.goal||{};
    const name=profile.name||root.dataset.name||'Learner';
    const subject=subjects[profile.subject]||assessment?.subject_name||'Learning profile';
    const score=assessment?`${assessment.score}/100`:'Waiting for your first score';
    const goalLabel=goal.target_score?`Goal · ${goal.target_score}/100`:'Set a practice goal';
    return `<section class="bm-panel bm-twin-hero"><div class="bm-twin-robot-stage" role="img" aria-label="Animated Brain Twin showing your profile and learning progress"><span class="bm-twin-orbit bm-twin-orbit-one"></span><span class="bm-twin-orbit bm-twin-orbit-two"></span><span class="bm-twin-data-chip bm-twin-chip-name">${esc(name)}</span><span class="bm-twin-data-chip bm-twin-chip-subject">${esc(subject)}</span><span class="bm-twin-data-chip bm-twin-chip-score">${esc(score)}</span><div class="bm-twin-robot"><span class="bm-twin-antenna"></span><span class="bm-twin-head"><span class="bm-twin-ear left"></span><span class="bm-twin-ear right"></span><span class="bm-twin-eye left"></span><span class="bm-twin-eye right"></span><span class="bm-twin-smile"></span></span><span class="bm-twin-neck"></span><span class="bm-twin-body"><span class="bm-twin-core"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a5 5 0 0 0-3 9v2h6v-2a5 5 0 0 0-3-9ZM9 17h6M10 20h4M7 8h1m8 0h1"/></svg></span><span class="bm-twin-arm left"></span><span class="bm-twin-arm right"></span></span></div><span class="bm-twin-spark bm-twin-spark-one">✦</span><span class="bm-twin-spark bm-twin-spark-two">✧</span></div><div class="bm-twin-hero-copy"><span class="bm-eyebrow">YOUR BRAIN TWIN</span><h2>Hi ${esc(name)}. Your learning, remembered.</h2><p>A living snapshot of your profile, goals, and progress. Your twin uses your local Chroma memory only when Personal history memory is enabled.</p><div class="bm-twin-memory"><div class="bm-twin-memory-head"><span class="bm-twin-memory-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 5h14v14H5zM8 9h8M8 12h5M8 15h8M3 8V3h5m8 0h5v5m0 8v5h-5m-8 0H3v-5"/></svg></span><div><span class="bm-eyebrow">PERSONAL RAG MEMORY · CHROMA</span><strong id="bm-twin-memory-status" role="status" aria-live="polite">Checking local memory…</strong></div></div><div id="bm-twin-memory-records" class="bm-twin-memory-records"><p class="bm-note">Connecting your profile and learning history.</p></div><a class="bm-twin-memory-link" href="/settings/">Manage memory and privacy →</a></div><div class="bm-twin-identity"><span>${esc(profile.occupation||'Learner')}</span><span>${esc(goalLabel)}</span></div></div></section>`;
  }
  async function loadTwinMemory(){
    const status=root.querySelector('#bm-twin-memory-status');
    const records=root.querySelector('#bm-twin-memory-records');
    if(!status||!records)return;
    if(!state.preferences?.memory){
      status.textContent='Memory is off';
      records.innerHTML='<p class="bm-note">Your profile and history stay outside Chroma until you enable Personal history memory.</p>';
      return;
    }
    status.textContent='Connecting to your local memory…';
    try{
      const memory=await api('memory');
      status.textContent=memory.status==='connected'?'Connected · account-scoped retrieval':'Memory is currently unavailable';
      records.innerHTML=memory.documents.length
        ?memory.documents.map((document,index)=>`<article class="bm-twin-memory-record" style="--record-index:${index}"><span>RECALLED CONTEXT ${String(index+1).padStart(2,'0')}</span><p>${esc(document.length>360?`${document.slice(0,357)}…`:document)}</p></article>`).join('')
        :'<p class="bm-note">No saved context yet. Your profile and completed learning summaries will appear here after memory is enabled.</p>';
    }catch(error){
      status.textContent='Memory connection needs attention';
      records.innerHTML=`<p class="bm-twin-memory-error" role="alert">${esc(error.message)}</p>`;
    }
  }
  function twin(){mode('learning');const t=window.BrainMaxI18n.translate;const a=latest();if(!a){root.innerHTML=head('BRAIN TWIN','Your learning mirror is empty.','Complete an assessment to build a data-led progress snapshot.')+twinMemoryHero()+`<div class="bm-panel">${button('/assessment/','Take assessment')}</div>`;loadTwinMemory();speak('Your Brain Twin grows from completed sessions. Start with one assessment.','growth-one','👀 Listening');return;}
    const strongest=Object.entries(a.topics).sort((x,y)=>y[1]-x[1])[0],weakest=Object.entries(a.topics).sort((x,y)=>x[1]-y[1])[0],allHistory=[...state.history].reverse(),history=allHistory.slice(-30),subjectHistory=allHistory.filter(item=>item.subject===a.subject).slice(-30),first=subjectHistory[0],last=subjectHistory[subjectHistory.length-1],change=first&&last?last.score-first.score:null,days=first&&last?Math.max(1,Math.round((new Date(last.completed_at)-new Date(first.completed_at))/86400000)+1):0,goal=state.goal||{},weeklyTarget=goal.weekly_sessions||3,weeklyCount=state.current_week_sessions||0;
    root.innerHTML=head('BRAIN TWIN','Your progress, in context.',t('A data-informed overview of your learning progress—not a diagnosis or fixed measure of ability.'),button('/settings/','Manage data',true))+twinMemoryHero(a)+
      `<div class="bm-grid four bm-twin-kpis"><section class="bm-panel"><span class="bm-note">LATEST PRACTICE SCORE</span><div class="bm-kpi">${a.score}<small>/100</small></div><p>${esc(a.date)} · ${esc(a.subject_name)}</p></section><section class="bm-panel"><span class="bm-note">RECORDED SESSIONS</span><div class="bm-kpi">${state.history_count??allHistory.length}</div><p>Across ${days||1} calendar day${days===1?'':'s'} in the visible history.</p></section><section class="bm-panel"><span class="bm-note">CHANGE IN SUBJECT TREND</span><div class="bm-kpi">${change===null?'—':`${change>=0?'+':''}${change}`}</div><p>${change===null?'At least two sessions in this subject are needed to compare.':`First to latest of the last ${subjectHistory.length} sessions in this subject. Repeated questions may contribute.`}</p></section><section class="bm-panel"><span class="bm-note">NEXT PRACTICE PROJECTION</span><div class="bm-kpi">${state.forecast?`${state.forecast.score}<small>/100</small>`:'—'}</div><p>${state.forecast?`Indicative range ${state.forecast.low}–${state.forecast.high}; ${state.forecast.confidence.replace('_',' ')} confidence based on ${state.forecast.sessions} session${state.forecast.sessions===1?'':'s'}. This is uncertain, not a promise.`:'Complete an assessment to establish a provisional projection.'}</p></section></div>
      <div class="bm-grid bm-twin-main"><section class="bm-panel"><div class="bm-section-heading"><div><span class="bm-eyebrow">HISTORY</span><h2>Score over time · ${esc(a.subject_name)}</h2><p>Independent score by completed session. The chart shows the latest 30 sessions in this subject.</p></div></div>${subjectHistory.length>1?trendChart(subjectHistory):'<p>One completed session in this subject is the starting point; more observations are needed for a trend.</p>'}<p class="bm-chart-caption">A short practice sample; repeated questions may affect score changes. Time is not penalized.</p></section>
      <section class="bm-panel"><span class="bm-eyebrow">LATEST SNAPSHOT</span><h2 style="margin-top:8px">${esc(a.level)}</h2><p>Strongest observed section: <strong>${esc(strongest[0])}</strong> (${strongest[1]}%).</p><p>Suggested next focus: <strong>${esc(weakest[0])}</strong> (${weakest[1]}%).</p>${topicBars(a)}<h3 class="bm-chart-subtitle">Skill radar</h3><p class="bm-radar-caption">Each axis shows one topic and its latest score out of 100. Compare the labels individually; polygon area is not a total score.</p>${skillRadar(a.topics)}${button('/learning-path/','Open learning path',true)}</section></div>
      <section class="bm-panel bm-history-panel"><div class="bm-section-heading"><div><span class="bm-eyebrow">PRACTICE RHYTHM</span><h2>12-week activity heatmap</h2><p>Completed assessment sessions by day. A quiet day is not a setback.</p></div></div>${activityHeatmap(state.activity)}</section>
      <section class="bm-panel bm-history-panel"><div class="bm-section-heading"><div><span class="bm-eyebrow">GOAL TRACKING</span><h2>Choose a steady target</h2><p>Track a score aim and a weekly practice rhythm. These guide practice; they do not predict outcomes.</p></div></div><div class="bm-grid"><form id="bm-goal-form" class="bm-goal-form"><div class="bm-field"><label for="bm-goal-score">Target practice score</label><input id="bm-goal-score" name="target_score" type="number" min="50" max="100" step="1" value="${goal.target_score||80}" required></div><div class="bm-field"><label for="bm-goal-weekly">Practice sessions per week</label><select id="bm-goal-weekly" name="weekly_sessions">${[1,2,3,4,5,6,7].map(count=>`<option value="${count}" ${weeklyTarget===count?'selected':''}>${count} per week</option>`).join('')}</select></div><div class="bm-field"><label for="bm-goal-date">Target date (optional)</label><input id="bm-goal-date" name="target_date" type="date" value="${esc(goal.target_date||'')}"></div><div id="bm-goal-error" class="bm-error" role="alert"></div><button class="bm-btn" type="submit">Save my goals</button></form><div class="bm-goal-progress"><span class="bm-note">LATEST SCORE TOWARD TARGET</span><div class="bm-kpi">${goal.target_score?`${state.goal_progress||0}<small>%</small>`:'—'}</div><div class="bm-progress"><span style="width:${goal.target_score?state.goal_progress||0:0}%"></span></div><p>${goal.target_score?`${a.score}/100 latest score toward ${goal.target_score}/100. This progress reflects the latest practice only.`:'Set a target score to begin tracking.'}</p><span class="bm-note">THIS WEEK'S PRACTICE</span><div class="bm-kpi">${weeklyCount}<small>/${weeklyTarget}</small></div><div class="bm-progress"><span style="width:${Math.min(100,weeklyCount*100/weeklyTarget)}%"></span></div><p>${weeklyCount>=weeklyTarget?'Weekly practice target reached.':`${weeklyTarget-weeklyCount} more session${weeklyTarget-weeklyCount===1?'':'s'} to your weekly target.`}</p></div></div></section>
      ${history.length?`<section class="bm-panel bm-history-panel"><div class="bm-section-heading"><div><span class="bm-eyebrow">SESSION LOG</span><h2>Practice history</h2><p>Use this timeline to understand what changed between sessions.</p></div></div><div class="bm-history-table-wrap"><table class="bm-history-table"><thead><tr><th>Date</th><th>Subject</th><th>Score</th><th>Accuracy</th><th>Change</th></tr></thead><tbody>${history.map((item,index)=>{const previous=history[index-1],delta=previous?item.score-previous.score:null;return `<tr><td>${esc(item.date)}</td><td>${esc(item.subject_name)}</td><td><strong>${item.score}/100</strong></td><td>${item.accuracy}%</td><td>${delta===null?'<span class="bm-note">Baseline</span>':`<span class="${delta>0?'bm-positive':delta<0?'bm-negative':'bm-note'}">${delta>0?'+':''}${delta} pts</span>`}</td></tr>`;}).join('')}</tbody></table></div></section>`:''}
      <p class="bm-note bm-evidence-note">Historical scores describe completed practice sessions only. Future projections are uncertain ranges, not predictions of ability or guarantees; repeated questions and a small sample can affect them.</p>`;
    root.querySelector('#bm-goal-form').onsubmit=event=>{event.preventDefault();const form=event.currentTarget;task(async()=>{try{const result=await api('goal',{target_score:Number(form.elements.namedItem('target_score').value),weekly_sessions:Number(form.elements.namedItem('weekly_sessions').value),target_date:form.elements.namedItem('target_date').value});state.goal=result.goal;state=await api('state');twin();}catch(e){root.querySelector('#bm-goal-error').textContent=e.message;}});};
    loadTwinMemory();
    speak(state.forecast?'Your practice history is ready. The dashboard includes an adaptive plan, activity rhythm, and uncertain projections.':history.length>1?'Your practice history is ready. Complete another session to see a cautious projection.':'One session gives a baseline and a very uncertain provisional projection.','growth-three','👀 Listening');}
  function settings(){mode('learning');const p=state.preferences||{};  root.innerHTML=head('SETTINGS','Your space, your choices.','Control coaching, history memory, voice, and the data kept with your account.')+`<div class="bm-grid"><section class="bm-panel"><h2>Brain preferences</h2><div class="bm-field"><label for="pref-language">Interface language</label><select id="pref-language" name="language"><option value="en" ${!p.language||p.language==='en'?'selected':''}>English</option><option value="fr" ${p.language==='fr'?'selected':''}>Français</option><option value="ar" ${p.language==='ar'?'selected':''}>العربية</option></select></div><div class="bm-toggle"><div><strong>AI coaching</strong><small>Off by default. When on, your messages and conversation context, goal, latest assessment score, and learning-challenge score and time may be sent to Groq (preferred when configured) or OpenRouter for feedback. When you ask why a path activity was assigned, its saved selection record is also included. Dated score and response-time trends are shared only when history sharing is enabled; retrieved Chroma history is included only when local memory is on.</small></div><input id="pref-ai" type="checkbox" ${p.ai?'checked':''} ${state.ai_ready?'':'disabled'} aria-label="Enable AI coaching"></div>${state.ai_ready?'':'<p class="bm-note">AI service is not configured; local guidance remains available.</p>'}<div class="bm-toggle"><div><strong>Personal history memory · local Chroma</strong><small>Off by default. When enabled, completed assessment summaries and learning-path topics, scores, attempts, times, and coaching feedback are indexed locally for your account only. Profile details and answer keys are not stored in RAG memory. This stays on your device and is not sent to AI unless AI coaching is separately enabled. Turning memory off deletes these local RAG records; your account history remains.</small></div><input id="pref-memory" type="checkbox" ${p.memory?'checked':''} aria-label="Enable local history memory"></div><div class="bm-toggle"><div><strong>Voice feedback</strong><small>Speak the same message shown on screen. Browser voice; no audio is stored.</small></div><input id="pref-voice" type="checkbox" ${p.voice?'checked':''}></div><div class="bm-toggle"><div><strong>Share progress history with AI coach</strong><small>Your dated session history and trends always appear in Brain Twin. This separate setting controls whether recent scores may be shared with the AI coach when AI coaching is enabled.</small></div><input id="pref-growth" type="checkbox" ${p.growth?'checked':''}></div><div class="bm-toggle"><div><strong>Mood check-in</strong><small>Optional self-report only. Brain adapts to what you select and does not infer emotions from answers or chat.</small></div><input id="pref-mood" type="checkbox" ${p.mood?'checked':''} aria-label="Enable optional mood check-in"></div><div id="bm-error" class="bm-error" role="alert"></div><button id="save-prefs" class="bm-btn">Save preferences</button></section><section class="bm-panel"><h2>Account & data</h2><p>Update your personal details or export your complete learning history.</p><p>${button('/accounts/profile/','Edit account',true)}</p><button id="export" class="bm-btn secondary">Download my data ↓</button><p class="bm-note">Export includes your profile, goals, preferences, completed assessments, and learning-path results.</p><button id="delete" class="bm-btn secondary" style="margin-top:10px">Delete learning data</button><p class="bm-note">This also removes local Chroma memory while keeping your account.</p></section></div>`;
    root.querySelector('#pref-memory').previousElementSibling.querySelector('small').textContent=window.BrainMaxI18n.translate('Off by default. When enabled, your name, learning subject, role, motivation, experience level, study-time preference, goals, assessment summaries, and learning-path results are indexed locally for your account. Passwords, email addresses, and raw answer keys are not stored. This stays on your device and is not sent to AI unless AI coaching is separately enabled. Turning memory off deletes these local RAG records; your account history remains.');
    root.querySelector('#save-prefs').onclick=()=>task(async()=>{const prefs={ai:root.querySelector('#pref-ai').checked,memory:root.querySelector('#pref-memory').checked,voice:root.querySelector('#pref-voice').checked,growth:root.querySelector('#pref-growth').checked,mood:root.querySelector('#pref-mood').checked,language:root.querySelector('#pref-language').value};const result=await api('preferences',prefs);state.preferences=result.preferences;window.BrainMaxI18n.apply(prefs.language);window.location.reload();});
    root.querySelector('#export').onclick=()=>{window.location.href='/api/export/';};
    root.querySelector('#delete').onclick=()=>{if(confirm('Delete your learning profile, assessment history, and local Chroma memory? Your account remains.'))task(async()=>{await api('reset',{});state=await api('state');settings();speak('Learning data deleted. Your account remains available.','neutral','✅ Done');});};speak('You control what Brain uses. AI coaching, local memory, voice, and sharing history with AI are optional.','neutral','👀 Listening');}

  function fallbackWorkspace(message='Your learning space is loading…') {
    const fallback = `
      <div class="bm-panel" style="padding:24px 28px;">
        <h2 style="margin:0 0 8px;">${esc(message)}</h2>
        <p style="margin:0;">Check your connection and refresh, or return to your profile to start again.</p>
      </div>
    `;
    if (root) root.innerHTML = fallback;
  }

  api('state').then(async data=>{
    try {
      state=data;
      if (window.BrainMaxI18n) {
        window.BrainMaxI18n.apply(state.preferences?.language||'en');
      }
      voiceOn=Boolean(state.preferences?.voice);
      const voiceBtn=document.getElementById('boltVoice');
      if (voiceBtn) voiceBtn.textContent=voiceOn?'🔊':'🔇';
      const moodWrap=document.getElementById('boltMoodWrap');
      if (moodWrap) moodWrap.hidden=!state.preferences?.mood;
      const aiHint=document.getElementById('boltAiHint');
      if (aiHint) aiHint.hidden=!state.ai_ready||Boolean(state.preferences?.ai);
      const url=new URL(window.location.href);
      let assessmentStartError = '';
      if(page==='assessment'&&url.searchParams.get('start')==='1'){
        url.searchParams.delete('start');
        window.history.replaceState(null,'',url.pathname+url.search+url.hash);
        if(state.profile?.subject&&(!state.assessment||state.assessment.completed)){
          try {
            state.assessment=await api('assessment',{});
          } catch (error) {
            assessmentStartError = error.message;
            console.error('Assessment start failed', error);
          }
        }
      }
      render();
      if (assessmentStartError) {
        const notice = document.createElement('div');
        notice.className = 'bm-panel bm-error';
        notice.setAttribute('role', 'alert');
        notice.textContent = `Unable to start the assessment: ${assessmentStartError}`;
        root.prepend(notice);
      }
    } catch (error) {
      console.error('Workspace render failed', error);
      fallbackWorkspace('Unable to load your workspace');
      speak('Your workspace needs a quick refresh.', 'confused', '❌ Error');
    }
  }).catch(e=>{
    console.error('Workspace state failed', e);
    fallbackWorkspace('Unable to load your workspace');
    if (window.BrainMaxI18n) {
      speak(e?.message || 'Please refresh the page.', 'confused', '❌ Error');
    }
  });
})();
