class Course:
    def __init__(self, title, code, days, startTime, endTime):
        self.t = title
        self.c = code
        self.d = days
        self.s = startTime
        self.e = endTime
    
    def descript(self):
        print(self.t + " (" + self.c + ") " + "has a lecture from " + self.s + " to " + self.e + " on " + self.d)

math4A = Course('MATH 4A - LIN ALG W/APPS', '30189', 'Monday, Wednesday, and Friday', '1:00 PM', '1:50 PM')
cmpsc16 = Course('CMPSC 16 - PROBLEM SOLVING I', '08128', 'Monday and Wednesday', '3:30 PM', '4:45 PM')
mus15 = Course('MUS 15 - MUSICAL COMMUNITIES', '35832', 'Tuesday and Thursday', '11:00 AM', '12:15 PM')


math4A.descript()
cmpsc16.descript()
mus15.descript()
