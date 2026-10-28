
  function onTurnstileSuccess(token) {
    $('#cf-turnstile-token').val(token);
}

function onTurnstileExpired() {
    $('#cf-turnstile-token').val('');
}

function onTurnstileError() {
    $('#cf-turnstile-token').val('');
}

function clearTurnstileError() {
    $('.error_message').empty();
}

$("#login-submit").on('click', async function (e) {
    e.preventDefault();

    const $turnstileInput = $('#cf-turnstile-token');
    const turnstileToken = $turnstileInput.val();
    const isTurnstileEnabled = $turnstileInput.length > 0;

    clearTurnstileError();

    if (isTurnstileEnabled && !turnstileToken) {
		$(".verification_code_sec").append(
            "<div class='captcha-error-inline error_message' style='color:#d93025;'><img src='/images/Icons_latest/common-validation-error.svg' alt='' style='flex-shrink:0;' />" +
        "<span>Please complete the verification.</span>" +
    "</div>"
        );
        return false;
    }

    ThumbmarkJS.setOption('exclude', ['canvas','locales','audio','plugins']);
    fingerprint = await ThumbmarkJS.getFingerprint()
    var publicKey = "-----BEGIN PUBLIC KEY-----\nMIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDMGRkZgSibfHkobhPnpcZQ0+8T\n7IPAN+L186ICCvkMBL4W8Q8ABbpwSZAcIdQmIrjGjz3aQE1xuu8fZG9MkSbzWC3E\n3pONL9FI4tqpeREEmjn0Goo8e9hua\/ykjko+0kZWRbUIQT5FGJ\/fiGK0Z7bAFVqg\nzw3Ikd8drZ8mga9RewIDAQAB\n-----END PUBLIC KEY-----\n";
    var encrypt = new JSEncrypt();
    encrypt.setPublicKey(publicKey);
    const username = encrypt.encrypt($('#UserLogin_username').val());
    const password = encrypt.encrypt($('#UserLogin_password').val());
    const redirectPage = $('#UserLogin_redirectpage').val();
    const inputDetails = {"UserLogin[username]": username, "UserLogin[password]": password, "UserLogin[redirectpage]": redirectPage, "login-submit":''}
    if($('#Field').is(":checked")){
	    inputDetails["Field"] = $('#Field').val();
    }
    if($('#UserLogin_verifyCode')){
	    inputDetails["UserLogin[verifyCode]"] = $('#UserLogin_verifyCode').val();	
    }

    if (isTurnstileEnabled && turnstileToken) {
        const fieldName = $turnstileInput.attr('name') || 'cf-turnstile-token';
        inputDetails[fieldName] = turnstileToken;
    }

    inputDetails['UserLogin[fingerprint]'] = fingerprint;
    const queryParams = window.location.search;
    const url = window.location.pathname+queryParams;
    $.redirect(url,inputDetails,"POST");
  });
