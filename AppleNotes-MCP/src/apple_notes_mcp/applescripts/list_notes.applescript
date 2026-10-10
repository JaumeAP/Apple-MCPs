on run argv
	set accountFilter to ""
	set folderFilter to ""
	if (count of argv) > 0 then set accountFilter to item 1 of argv
	if (count of argv) > 1 then set folderFilter to item 2 of argv
	set jsonItems to {}
	tell application "Notes"
		repeat with acc in accounts
			set accName to my safe_text(name of acc)
			set accId to my safe_text(id of acc)
			if accountFilter is not "" and accName is not accountFilter then
				-- skip
			else
				repeat with fld in folders of acc
					set fldId to my safe_text(id of fld)
					set fldName to my safe_text(name of fld)
					if folderFilter is not "" and fldId is not folderFilter then
						-- skip
					else
						set folderItems to missing value
						try
							set folderItems to my folder_notes_json(accId, accName, fldId, fldName, fld)
						end try
						if folderItems is missing value then
							-- Bulk fetch failed: fall back to per-note property reads.
							set folderItems to {}
							repeat with n in notes of fld
								set end of folderItems to my note_json(accId, accName, fldId, fldName, n)
							end repeat
						end if
						set jsonItems to jsonItems & folderItems
					end if
				end repeat
			end if
		end repeat
	end tell
	return "{" & quote & "items" & quote & ":[" & my join_list(jsonItems, ",") & "]}"
end run

on note_json(accountId, accountName, folderId, folderName, n)
	tell application "Notes" to set rawNoteId to id of n
	set noteId to my safe_text(rawNoteId)
	-- `n` is positional (item i of notes): pin it to the id so a concurrent
	-- reorder cannot pair this id with another note's properties.
	tell application "Notes" to set n to note id noteId
	set titleText to ""
	set plainText to ""
	set createdEpoch to 0
	set modifiedEpoch to 0
		set sharedValue to false
		set attachmentCount to 0
		try
			tell application "Notes" to set rawTitleText to name of n
			set titleText to my safe_text(rawTitleText)
		end try
	try
		tell application "Notes" to set rawPlainText to plaintext of n
		set plainText to my safe_text(rawPlainText)
	end try
	try
		tell application "Notes" to set rawCreatedDate to creation date of n
		set createdEpoch to my date_to_epoch(rawCreatedDate)
	end try
	try
		tell application "Notes" to set rawModifiedDate to modification date of n
		set modifiedEpoch to my date_to_epoch(rawModifiedDate)
	end try
		try
			tell application "Notes" to set sharedValue to shared of n
		end try
	try
		tell application "Notes" to set attachmentCount to count of attachments of n
	end try
	return my note_record_json(noteId, titleText, accountId, accountName, folderId, folderName, createdEpoch, modifiedEpoch, sharedValue, attachmentCount, plainText)
end note_json

-- Reads each property for every note of the folder in one Apple Event per
-- property instead of one per note and property. Errors (including a folder
-- that changed between reads) propagate so the caller can fall back.
-- The parallel lists are only paired by position, so the ids are read again
-- after the other properties: any add, delete or reorder in between (an edit
-- moves a note to the top) changes the id list and forces the per-note path,
-- so a body is never paired with another note's id.
-- ponytail: two id reads miss a reorder that is undone in between; read properties of every note.
on folder_notes_json(accountId, accountName, folderId, folderName, fld)
	tell application "Notes"
		set noteIds to id of every note of fld
		set noteNames to name of every note of fld
		set notePlainTexts to plaintext of every note of fld
		set createdDates to creation date of every note of fld
		set modifiedDates to modification date of every note of fld
		set sharedValues to shared of every note of fld
		set noteIdsAfter to id of every note of fld
	end tell
	if noteIdsAfter is not noteIds then error "Notes changed while the folder was being listed."
	set noteCount to count of noteIds
	repeat with propertyValues in {noteNames, notePlainTexts, createdDates, modifiedDates, sharedValues}
		if (count of propertyValues) is not noteCount then error "Notes changed while the folder was being listed."
	end repeat
	set folderItems to {}
	repeat with i from 1 to noteCount
		set noteId to my safe_text(item i of noteIds)
		set createdEpoch to 0
		set modifiedEpoch to 0
		set attachmentCount to 0
		try
			set createdEpoch to my date_to_epoch(item i of createdDates)
		end try
		try
			set modifiedEpoch to my date_to_epoch(item i of modifiedDates)
		end try
		set sharedValue to item i of sharedValues
		if sharedValue is not true then set sharedValue to false
		-- Notes has no reliable bulk form for a per-note element count.
		-- ponytail: attachment count is one Apple Event per note; batch it if Notes ever offers a bulk count.
		try
			tell application "Notes" to set attachmentCount to count of attachments of note id noteId
		end try
		set end of folderItems to my note_record_json(noteId, my safe_text(item i of noteNames), accountId, accountName, folderId, folderName, createdEpoch, modifiedEpoch, sharedValue, attachmentCount, my safe_text(item i of notePlainTexts))
	end repeat
	return folderItems
end folder_notes_json

on note_record_json(noteId, titleText, accountId, accountName, folderId, folderName, createdEpoch, modifiedEpoch, sharedValue, attachmentCount, plainText)
	set noteJson to "{" & ¬
		quote & "note_id" & quote & ":" & my json_string(noteId) & "," & ¬
		quote & "title" & quote & ":" & my json_string(titleText) & "," & ¬
		quote & "account_id" & quote & ":" & my json_string(accountId) & "," & ¬
		quote & "account_name" & quote & ":" & my json_string(accountName) & "," & ¬
		quote & "folder_id" & quote & ":" & my json_string(folderId) & "," & ¬
		quote & "folder_name" & quote & ":" & my json_string(folderName) & "," & ¬
		quote & "created_epoch" & quote & ":" & createdEpoch & "," & ¬
		quote & "modified_epoch" & quote & ":" & modifiedEpoch & "," & ¬
			quote & "shared" & quote & ":" & my json_boolean(sharedValue) & "," & ¬
		quote & "attachment_count" & quote & ":" & attachmentCount & "," & ¬
		quote & "plaintext" & quote & ":" & my json_string(plainText) & "}"
	return noteJson
end note_record_json

on date_to_epoch(dateValue)
	set epochDate to current date
	set year of epochDate to 1970
	set month of epochDate to January
	set day of epochDate to 1
	set time of epochDate to 0
	set elapsedSeconds to dateValue - epochDate
	if elapsedSeconds < 0 then return "0"
	-- Integers stop at 2^29, so any date after 1987 gives a real, and a real
	-- becomes text with the locale's decimal separator ("1,79E+9" on ca_ES),
	-- which is invalid JSON. Build the digits from two small integers.
	set highPart to elapsedSeconds div 100000
	set lowPart to (elapsedSeconds mod 100000) as integer
	if highPart is 0 then return lowPart as text
	return (highPart as text) & (text -5 thru -1 of ("0000" & lowPart))
end date_to_epoch

on safe_text(valueText)
	try
		return valueText as text
	on error
		return ""
	end try
end safe_text

on json_boolean(boolValue)
	if boolValue then
		return "true"
	end if
	return "false"
end json_boolean

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
