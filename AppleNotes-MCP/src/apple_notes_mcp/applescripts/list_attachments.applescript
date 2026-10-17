on run argv
	set targetNoteId to item 1 of argv
	set jsonItems to {}
	tell application "Notes"
		-- Look the note up by id in one Apple Event: a positional walk races with
		-- concurrent reorders and could read another note's attachments.
		try
			set attachmentNames to name of every attachment of note id targetNoteId
		on error
			return "{" & quote & "found" & quote & ":false}"
		end try
	end tell
	repeat with attachmentName in attachmentNames
		set end of jsonItems to "{" & quote & "name" & quote & ":" & my json_string(my safe_text(contents of attachmentName)) & "}"
	end repeat
	return "{" & quote & "found" & quote & ":true," & quote & "items" & quote & ":[" & my join_list(jsonItems, ",") & "]}"
end run

on safe_text(valueText)
	try
		return valueText as text
	on error
		return ""
	end try
end safe_text

on json_string(valueText)
	set backslash to ASCII character 92
	set escapedText to my replace_text(valueText as text, backslash, backslash & backslash)
	set escapedText to my replace_text(escapedText, quote, backslash & quote)
	set escapedText to my replace_text(escapedText, return, backslash & "n")
	set escapedText to my replace_text(escapedText, linefeed, backslash & "n")
	return quote & escapedText & quote
end json_string

on replace_text(sourceText, findText, replaceText)
	set originalDelimiters to AppleScript's text item delimiters
	set AppleScript's text item delimiters to findText
	set sourceItems to text items of sourceText
	set AppleScript's text item delimiters to replaceText
	set resultText to sourceItems as text
	set AppleScript's text item delimiters to originalDelimiters
	return resultText
end replace_text

on join_list(itemList, delimiterText)
	if (count of itemList) is 0 then
		return ""
	end if
	set originalDelimiters to AppleScript's text item delimiters
	set AppleScript's text item delimiters to delimiterText
	set joinedText to itemList as text
	set AppleScript's text item delimiters to originalDelimiters
	return joinedText
end join_list
